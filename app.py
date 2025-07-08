import sqlite3
import json
import re
from flask import Flask, send_from_directory, g, request, jsonify
from datetime import datetime, timezone
import os
from werkzeug.exceptions import NotFound # Import NotFound specifically

app = Flask(__name__)

# Define the database file name
DATABASE = 'maestro_score.db'

# Global variable for the PDF storage path. Initially None, will be loaded from DB or set by user.
PDF_STORAGE_PATH_VAR = None 

# --- Database Helper Functions ---

def get_db():
    """
    Establishes a connection to the SQLite database.
    The connection is stored in Flask's 'g' object to be reused within the same request.
    """
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        # Enable row_factory to get dictionary-like rows
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    """
    Closes the database connection at the end of a request.
    """
    db = g.pop('db', None)
    if db is not None:
        db.close()

def scan_pdfs_and_populate_db():
    """
    Scans the PDF_STORAGE_PATH_VAR, clears existing library data,
    and populates the LibraryItem table in the database.
    This reconstructs the folder structure and PDF entries.
    This function will only proceed if PDF_STORAGE_PATH_VAR is a valid directory.
    """
    db = get_db()
    cursor = db.cursor()

    # --- Preserve playlist associations ---
    print("Backing up playlist associations...")
    cursor.execute("""
        SELECT ps.playlist_id, li.pdf_url, ps.order_index
        FROM PlaylistSong ps
        JOIN LibraryItem li ON ps.library_item_id = li.id
    """)
    playlist_backup = [dict(row) for row in cursor.fetchall()]
    print(f"Backed up {len(playlist_backup)} playlist entries.")

    # Clear existing library items and playlist songs before scanning
    # This is crucial to refresh the library from the file system
    cursor.execute("DELETE FROM PlaylistSong") # Must delete playlist songs first due to FK constraint
    cursor.execute("DELETE FROM LibraryItem")
    db.commit()
    print("Cleared existing library and playlist data for refresh.")

    # Only proceed with scanning if PDF_STORAGE_PATH_VAR is set and is a valid directory
    if not PDF_STORAGE_PATH_VAR or not os.path.isdir(PDF_STORAGE_PATH_VAR):
        print(f"Warning: PDF_STORAGE_PATH_VAR is not set or is not a valid directory ('{PDF_STORAGE_PATH_VAR}'). Skipping PDF scan.")
        # Ensure 'Root' folder exists even if no PDFs are scanned
        cursor.execute("INSERT OR IGNORE INTO LibraryItem (id, name, type, parent_id, pdf_url, date_created, date_last_played) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (1, "Root", "folder", None, None, datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'), None))
        db.commit()
        return

    print(f"Scanning PDF_STORAGE_PATH_VAR: {PDF_STORAGE_PATH_VAR}")

    # Ensure the conceptual 'Root' folder entry exists for hierarchical structure
    cursor.execute("INSERT OR IGNORE INTO LibraryItem (id, name, type, parent_id, pdf_url, date_created, date_last_played) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (1, "Root", "folder", None, None, datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'), None))
    root_db_id = 1 # The ID for the Root folder is always 1

    # Recursive function to build the nested structure
    def insert_scanned_item(abs_path, relative_path, parent_db_id):
        item_name = os.path.basename(abs_path)
        item_type = "folder" if os.path.isdir(abs_path) else "pdf"
        
        # Only process PDF files (ending with .pdf) and folders
        if item_type == "pdf" and not item_name.lower().endswith('.pdf'):
            return # Skip non-PDF files

        date_created = datetime.fromtimestamp(os.path.getctime(abs_path), tz=timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        date_last_played = datetime.fromtimestamp(os.path.getmtime(abs_path), tz=timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z') # Using modification time as last played

        cursor.execute("""
            INSERT INTO LibraryItem (name, type, parent_id, pdf_url, date_created, date_last_played)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (item_name, item_type, parent_db_id,
              relative_path if item_type == "pdf" else None, date_created, date_last_played))
        item_id = cursor.lastrowid

        # If it's a folder, recursively scan its contents
        if item_type == "folder":
            for entry_name in os.listdir(abs_path):
                entry_abs_path = os.path.join(abs_path, entry_name)
                # Build relative path correctly for sub-items
                entry_relative_path = os.path.join(relative_path, entry_name) if relative_path else entry_name
                insert_scanned_item(entry_abs_path, entry_relative_path, item_id)
    
    # Start scanning from the PDF_STORAGE_PATH_VAR, linking its direct contents to the 'Root' folder in the database
    for entry_name in os.listdir(PDF_STORAGE_PATH_VAR):
        entry_abs_path = os.path.join(PDF_STORAGE_PATH_VAR, entry_name)
        # Initial relative path for top-level items is just their name
        insert_scanned_item(entry_abs_path, entry_name, root_db_id)
    
    db.commit()
    print("Library scanned and database populated.")

    # --- Restore playlist associations ---
    print("Restoring playlist associations...")
    restored_count = 0
    if playlist_backup:
        # Create a map of pdf_url to new id for faster lookups
        cursor.execute("SELECT id, pdf_url FROM LibraryItem WHERE type = 'pdf'")
        new_id_map = {row['pdf_url']: row['id'] for row in cursor.fetchall()}

        for entry in playlist_backup:
            playlist_id = entry['playlist_id']
            pdf_url = entry['pdf_url']
            order_index = entry['order_index']

            new_song_id = new_id_map.get(pdf_url)

            if new_song_id:
                try:
                    cursor.execute(
                        "INSERT INTO PlaylistSong (playlist_id, library_item_id, order_index) VALUES (?, ?, ?)",
                        (playlist_id, new_song_id, order_index)
                    )
                    restored_count += 1
                except sqlite3.IntegrityError:
                    print(f"Warning: Could not restore playlist entry for PDF '{pdf_url}' - possibly a duplicate.")
            else:
                print(f"Warning: PDF '{pdf_url}' from a playlist was not found after rescan. It will be removed from the playlist.")
    
    db.commit()
    print(f"Restored {restored_count} playlist entries.")

def init_db():
    """
    Initializes the database: creates tables and loads initial configuration.
    """
    global PDF_STORAGE_PATH_VAR # Declare intent to modify global variable
    db = get_db()
    cursor = db.cursor()

    # Create Config table to store key-value settings
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # Create LibraryItem table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS LibraryItem (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL, -- 'folder' or 'pdf'
            parent_id INTEGER,
            pdf_url TEXT, -- Now stores relative paths like 'Classical/Beethoven.pdf'
            date_created TEXT,
            date_last_played TEXT,
            FOREIGN KEY (parent_id) REFERENCES LibraryItem (id)
        )
    ''')

    # Add metadata columns if they don't exist (for backward compatibility)
    metadata_columns = {
        'title': 'TEXT', 'composer': 'TEXT', 'genre': 'TEXT', 'tag': 'TEXT',
        'label': 'TEXT', 'rating': 'TEXT', 'difficulty': 'TEXT', 'playtime': 'TEXT',
        'key': 'TEXT', 'time': 'TEXT'
    }
    cursor.execute("PRAGMA table_info(LibraryItem)")
    existing_columns = {row['name'] for row in cursor.fetchall()}
    for col_name, col_type in metadata_columns.items():
        if col_name not in existing_columns:
            cursor.execute(f"ALTER TABLE LibraryItem ADD COLUMN {col_name} {col_type}")
            print(f"Added column '{col_name}' to LibraryItem table.")

    # Create Playlist table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Playlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    ''')

    # Create PlaylistSong table (junction table for many-to-many relationship)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS PlaylistSong (
            playlist_id INTEGER,
            library_item_id INTEGER,
            order_index INTEGER NOT NULL,
            PRIMARY KEY (playlist_id, library_item_id),
            FOREIGN KEY (playlist_id) REFERENCES Playlist (id),
            FOREIGN KEY (library_item_id) REFERENCES LibraryItem (id)
        )
    ''')
    db.commit()

    # Load PDF storage path from Config table
    cursor.execute("SELECT value FROM Config WHERE key = 'pdf_storage_path'")
    config_row = cursor.fetchone()
    if config_row:
        PDF_STORAGE_PATH_VAR = config_row['value']
        print(f"Loaded PDF_STORAGE_PATH_VAR from config: {PDF_STORAGE_PATH_VAR}")
    else:
        print("PDF_STORAGE_PATH_VAR not found in config. It remains unset.")

    # Check if the library has any items. If not, perform an initial scan.
    # This prevents wiping the database on every application start.
    cursor.execute("SELECT COUNT(id) FROM LibraryItem")
    item_count = cursor.fetchone()[0]
    if item_count == 0:
        print("Library is empty. Performing initial scan.")
        scan_pdfs_and_populate_db()
    else:
        print(f"Library contains {item_count} items. Skipping scan on startup.")

    # Also ensure initial playlists are present if the Playlist table is empty
    cursor.execute("SELECT COUNT(*) FROM Playlist")
    if cursor.fetchone()[0] == 0:
        print("Populating initial playlist data (names only)...")
        initial_playlists_names = [
            'All Time Favorites',
            'Study Tunes',
            'New Arrivals'
        ]
        for playlist_name in initial_playlists_names:
            cursor.execute("INSERT INTO Playlist (name) VALUES (?)", (playlist_name,))
            db.commit() # Commit each playlist creation
        print("Initial playlists created.")


# Register the close_db function to be called after each request
app.teardown_appcontext(close_db)

# Call init_db to set up the database when the app starts
with app.app_context():
    init_db()

# --- API Endpoints ---

# Route to serve local PDF files from the PDF_STORAGE_PATH_VAR
@app.route('/local_pdfs/<path:filename>')
def serve_pdf(filename):
    """
    Serves PDF files from the configured PDF_STORAGE_PATH_VAR.
    The <path:filename> converter allows the filename to include slashes,
    enabling subdirectories within the storage path.
    """
    # Ensure PDF_STORAGE_PATH_VAR is set before attempting to serve
    if not PDF_STORAGE_PATH_VAR or not os.path.isdir(PDF_STORAGE_PATH_VAR):
        print(f"Error: PDF_STORAGE_PATH_VAR is not set or invalid when trying to serve '{filename}'. Current path: '{PDF_STORAGE_PATH_VAR}'")
        return jsonify({"error": "PDF storage path not configured or invalid on server."}), 500

    try:
        print(f"Attempting to serve: {filename}")
        print(f"PDF_STORAGE_PATH_VAR: {PDF_STORAGE_PATH_VAR}")
        
        # Construct the absolute path to the file expected to be served
        full_path_to_file = os.path.join(PDF_STORAGE_PATH_VAR, filename)
        
        print(f"Full path to file being checked: {full_path_to_file}")
        print(f"Does file exist at full path? {os.path.exists(full_path_to_file)}")

        # Ensure the resolved path is still within the PDF_STORAGE_PATH_VAR
        # This is a security check against directory traversal
        abs_storage_path = os.path.abspath(PDF_STORAGE_PATH_VAR)
        abs_requested_path = os.path.abspath(full_path_to_file)

        if not abs_requested_path.startswith(abs_storage_path):
            print(f"Security Warning: Attempted path outside storage directory: {abs_requested_path}")
            return jsonify({"error": "Unauthorized access to file"}), 403

        # send_from_directory handles security like preventing directory traversal internally
        return send_from_directory(PDF_STORAGE_PATH_VAR, filename)
    except NotFound: # Catch NotFound specifically from send_from_directory
        print(f"Flask NotFound: PDF file not found by send_from_directory at: {full_path_to_file}")
        return jsonify({"error": "PDF file not found on server."}), 404
    except Exception as e:
        print(f"General Error serving PDF '{filename}': {e}")
        return jsonify({"error": f"An unexpected error occurred while serving the PDF: {str(e)}"}), 500


@app.route('/api/library', methods=['GET'])
def get_library():
    """
    API endpoint to fetch the entire library structure.
    It reconstructs the hierarchical folder structure from the flat database.
    """
    db = get_db()
    cursor = db.cursor()

    # Fetch all library items, aliasing pdf_url as file_path for frontend compatibility
    cursor.execute("SELECT id, name, type, parent_id, pdf_url AS file_path, date_created, date_last_played FROM LibraryItem")
    items_flat = [dict(row) for row in cursor.fetchall()]

    # Find the conceptual "Root" folder ID
    root_folder_id = None
    for item in items_flat:
        if item['parent_id'] is None and item['name'] == 'Root':
            root_folder_id = item['id']
            break
    
    if root_folder_id is None:
        # If no root folder, database might be truly empty or not initialized correctly
        # This should ideally not happen if init_db() correctly inserts the Root.
        print("Warning: 'Root' folder not found in LibraryItem table. Returning empty structure.")
        return jsonify({"name": "Root", "type": "folder", "contents": []})

    # Recursive function to build the nested structure
    def build_nested_structure(flat_list, current_parent_id):
        children = []
        for item in flat_list:
            if item['parent_id'] == current_parent_id:
                child_copy = dict(item) # Create a copy to modify
                if child_copy['type'] == 'folder':
                    child_copy['contents'] = build_nested_structure(flat_list, child_copy['id'])
                children.append(child_copy)
        
        # Sort folders first, then PDFs, then by name (case-insensitive for name)
        return sorted(children, key=lambda x: (x['type'] == 'pdf', x['name'].lower()))

    # Build the contents of the main 'Library' which are direct children of the 'Root' folder
    library_contents = build_nested_structure(items_flat, root_folder_id)

    return jsonify({"name": "Root", "type": "folder", "contents": library_contents})


@app.route('/api/library/<int:item_id>/play', methods=['POST'])
def update_library_item_play_date(item_id):
    """
    API endpoint to update the date_last_played for a specific library item (PDF).
    """
    db = get_db()
    try:
        current_time_iso = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

        db.execute("UPDATE LibraryItem SET date_last_played = ? WHERE id = ?",
                   (current_time_iso, item_id))
        db.commit()
        return jsonify({"message": f"date_last_played for item {item_id} updated."}), 200
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/library/<int:item_id>', methods=['GET'])
def get_library_item(item_id):
    """
    API endpoint to fetch a single library item by its ID.
    Used by index.html to get the PDF URL when playing from a playlist.
    """
    db = get_db()
    cursor = db.cursor()
    # Alias pdf_url as file_path to match frontend expectation
    cursor.execute("""
        SELECT id, name, type, pdf_url AS file_path, date_created, date_last_played,
               title, composer, genre, tag, label, rating, difficulty, playtime, key, time
        FROM LibraryItem WHERE id = ?
    """, (item_id,))
    item = cursor.fetchone()
    if item:
        return jsonify(dict(item)), 200
    return jsonify({"error": "Item not found"}), 404

@app.route('/api/rescan_library', methods=['POST'])
def rescan_library_api():
    """
    API endpoint to trigger a rescan and repopulation of the library.
    """
    try:
        with app.app_context(): # Ensure we are in the app context for DB operations
            scan_pdfs_and_populate_db()
        return jsonify({"message": "Library scan initiated and database updated successfully."}), 200
    except Exception as e:
        print(f"Error during library rescan: {e}")
        return jsonify({"error": f"Failed to rescan library: {str(e)}"}), 500

@app.route('/api/update_pdf_path', methods=['POST'])
def update_pdf_path():
    """
    API endpoint to update the PDF_STORAGE_PATH_VAR.
    Requires 'new_path' in the request JSON body.
    Triggers a rescan of the library after updating the path.
    """
    global PDF_STORAGE_PATH_VAR # Declare intent to modify the global variable
    db = get_db()
    data = request.get_json()
    new_path = data.get('new_path')

    if not new_path:
        return jsonify({"error": "New path is required"}), 400

    # Basic path sanitization for security (prevents relative paths like ../../)
    normalized_path = os.path.normpath(new_path)
    
    # Check if the new path exists and is a directory
    if not os.path.isdir(normalized_path):
        try:
            os.makedirs(normalized_path) # Try to create the directory if it doesn't exist
            print(f"Created new PDF storage directory: {normalized_path}")
        except OSError as e:
            return jsonify({"error": f"Invalid or inaccessible path: {str(e)}"}), 400

    # Update global variable
    PDF_STORAGE_PATH_VAR = normalized_path
    print(f"PDF_STORAGE_PATH_VAR updated to: {PDF_STORAGE_PATH_VAR}")

    # Save the updated path to the Config table
    try:
        cursor = db.cursor()
        cursor.execute("INSERT OR REPLACE INTO Config (key, value) VALUES (?, ?)", ('pdf_storage_path', PDF_STORAGE_PATH_VAR))
        db.commit()
        print("PDF storage path saved to database.")
    except sqlite3.Error as e:
        db.rollback()
        print(f"Error saving PDF storage path to config: {e}")
        return jsonify({"error": f"Failed to save PDF storage path: {str(e)}"}), 500


    try:
        with app.app_context():
            scan_pdfs_and_populate_db() # Rescan with the new path
        return jsonify({"message": f"Library path updated to '{new_path}' and rescanned."}), 200
    except Exception as e:
        print(f"Error during library rescan after path update: {e}")
        return jsonify({"error": f"Failed to rescan library with new path: {str(e)}"}), 500

@app.route('/api/config/pdf_storage_path', methods=['GET'])
def get_pdf_storage_path():
    """
    API endpoint to get the currently configured PDF storage path.
    """
    if PDF_STORAGE_PATH_VAR:
        return jsonify({"path": PDF_STORAGE_PATH_VAR}), 200
    else:
        # Return a specific status even if path is not set, but indicate it's not an error
        return jsonify({"path": None, "message": "PDF storage path is not set."}), 200

@app.route('/api/library/rename/<int:item_id>', methods=['POST'])
def rename_library_item(item_id):
    """
    API endpoint to rename a PDF item in the library and its corresponding file on the server.
    Requires 'new_name' in the request JSON body.
    """
    db = get_db()
    data = request.get_json()
    new_name = data.get('new_name')

    if not new_name:
        return jsonify({"error": "New name is required"}), 400

    cursor = db.cursor()
    cursor.execute("SELECT name, pdf_url, type FROM LibraryItem WHERE id = ?", (item_id,))
    item = cursor.fetchone()

    if not item:
        return jsonify({"error": "Item not found"}), 404
    
    if item['type'] != 'pdf':
        return jsonify({"error": "Only PDF items can be renamed this way."}), 400

    old_relative_path = item['pdf_url']
    old_filename = os.path.basename(old_relative_path)
    old_dirname = os.path.dirname(old_relative_path)
    
    # Ensure the new name retains the .pdf extension if it's a PDF
    if not new_name.lower().endswith('.pdf'):
        new_name += '.pdf'
    
    new_relative_path = os.path.join(old_dirname, new_name)

    # Use the globally configured PDF_STORAGE_PATH_VAR
    if not PDF_STORAGE_PATH_VAR:
        return jsonify({"error": "PDF storage path is not configured on the server."}), 500

    old_abs_path = os.path.join(PDF_STORAGE_PATH_VAR, old_relative_path)
    new_abs_path = os.path.join(PDF_STORAGE_PATH_VAR, new_relative_path)

    # Basic check to prevent overwriting existing files or invalid paths
    if os.path.exists(new_abs_path) and old_abs_path != new_abs_path:
        return jsonify({"error": "A file with this new name already exists in the folder."}), 409
    
    if not os.path.exists(old_abs_path):
        return jsonify({"error": "Original PDF file not found on server."}), 404

    try:
        os.rename(old_abs_path, new_abs_path)
        
        # Update database entry
        cursor.execute("UPDATE LibraryItem SET name = ?, pdf_url = ? WHERE id = ?",
                       (new_name, new_relative_path, item_id))
        db.commit()
        return jsonify({"message": f"Successfully renamed '{old_filename}' to '{new_name}'."}), 200
    except OSError as e:
        db.rollback()
        return jsonify({"error": f"Server file system error: {str(e)}"}), 500
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/api/library/<int:item_id>/metadata', methods=['POST'])
def update_library_item_metadata(item_id):
    """
    API endpoint to update various metadata fields for a specific library item.
    """
    db = get_db()
    data = request.get_json()

    # These are the fields the frontend can update.
    allowed_fields = [
        'title', 'composer', 'genre', 'tag', 'label', 'rating',
        'difficulty', 'playtime', 'key', 'time'
    ]

    # Build the SET part of the SQL query dynamically and safely
    set_clauses = []
    values = []
    for field in allowed_fields:
        if field in data:
            value = data.get(field)

            # Server-side validation
            if field == 'rating' and value:
                try:
                    float(value) # Check if it can be cast to a number
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid rating format. Must be a number."}), 400
            
            if field == 'playtime' and value:
                if not re.match(r'^\d{1,2}:\d{2}$', value):
                    return jsonify({"error": "Invalid playtime format. Must be MM:SS."}), 400

            set_clauses.append(f"{field} = ?")
            values.append(value)

    if not set_clauses:
        return jsonify({"error": "No valid metadata fields provided for update."}), 400

    sql_query = f"UPDATE LibraryItem SET {', '.join(set_clauses)} WHERE id = ?"
    values.append(item_id)

    try:
        cursor = db.cursor()
        cursor.execute(sql_query, tuple(values))
        if cursor.rowcount == 0:
            return jsonify({"error": "Item not found"}), 404
        db.commit()
        return jsonify({"message": "Metadata updated successfully."}), 200
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/api/playlists', methods=['GET'])
def get_playlists():
    """
    API endpoint to fetch all playlists.
    Does not fetch songs within playlists to keep payload small.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, name FROM Playlist ORDER BY name ASC")
    playlists_rows = cursor.fetchall()
    
    playlists = []
    for playlist_row in playlists_rows:
        playlist_dict = dict(playlist_row)
        # Get song count for each playlist
        cursor.execute("SELECT COUNT(*) FROM PlaylistSong WHERE playlist_id = ?", (playlist_dict['id'],))
        playlist_dict['song_count'] = cursor.fetchone()[0]
        playlists.append(playlist_dict)
    
    return jsonify(playlists), 200

@app.route('/api/playlists', methods=['POST'])
def create_playlist():
    """
    API endpoint to create a new playlist.
    Requires 'name' in the request JSON body.
    """
    db = get_db()
    data = request.get_json()
    name = data.get('name')

    if not name:
        return jsonify({"error": "Playlist name is required"}), 400

    try:
        cursor = db.cursor()
        cursor.execute("INSERT INTO Playlist (name) VALUES (?)", (name,))
        db.commit()
        return jsonify({"message": "Playlist created successfully", "id": cursor.lastrowid, "name": name}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Playlist with this name already exists"}), 409
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/playlists/<int:playlist_id>', methods=['GET'])
def get_single_playlist(playlist_id):
    """
    API endpoint to fetch a single playlist with its songs.
    Songs are ordered by their 'order_index'.
    """
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, name FROM Playlist WHERE id = ?", (playlist_id,))
    playlist = cursor.fetchone()

    if not playlist:
        return jsonify({"error": "Playlist not found"}), 404

    # Fetch songs for the playlist, joining with LibraryItem to get song details
    # Alias li.pdf_url as file_path to match frontend expectation
    cursor.execute("""
        SELECT
            li.id, li.name, li.type, li.pdf_url AS file_path, li.date_created, li.date_last_played,
            ps.order_index
        FROM PlaylistSong ps
        JOIN LibraryItem li ON ps.library_item_id = li.id
        WHERE ps.playlist_id = ?
        ORDER BY ps.order_index ASC
    """, (playlist_id,))
    songs_rows = cursor.fetchall()

    songs = [dict(song) for song in songs_rows]
    playlist_dict = dict(playlist)
    playlist_dict['songs'] = songs

    return jsonify(playlist_dict), 200

@app.route('/api/playlists/<int:playlist_id>', methods=['DELETE'])
def delete_playlist(playlist_id):
    """
    API endpoint to delete a playlist and all its song associations.
    """
    db = get_db()
    try:
        cursor = db.cursor()
        # First, delete all song associations for this playlist to satisfy foreign key constraints
        cursor.execute("DELETE FROM PlaylistSong WHERE playlist_id = ?", (playlist_id,))
        # Then, delete the playlist itself
        cursor.execute("DELETE FROM Playlist WHERE id = ?", (playlist_id,))
        
        if cursor.rowcount == 0:
            return jsonify({"error": "Playlist not found"}), 404
            
        db.commit()
        return jsonify({"message": "Playlist deleted successfully"}), 200
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500

@app.route('/api/playlists/<int:playlist_id>/songs', methods=['POST'])
def add_song_to_playlist(playlist_id):
    """
    API endpoint to add a song to a playlist.
    Requires 'library_item_id' in the request JSON body.
    """
    db = get_db()
    data = request.get_json()
    library_item_id = data.get('library_item_id')

    if not library_item_id:
        return jsonify({"error": "Library item ID is required"}), 400

    try:
        cursor = db.cursor()
        # Check if song already exists in playlist
        cursor.execute("SELECT COUNT(*) FROM PlaylistSong WHERE playlist_id = ? AND library_item_id = ?",
                       (playlist_id, library_item_id))
        if cursor.fetchone()[0] > 0:
            return jsonify({"error": "Song already in playlist"}), 409

        # Determine the next order_index
        cursor.execute("SELECT MAX(order_index) FROM PlaylistSong WHERE playlist_id = ?", (playlist_id,))
        max_order = cursor.fetchone()[0]
        next_order_index = (max_order + 1) if max_order is not None else 0

        cursor.execute("""
            INSERT INTO PlaylistSong (playlist_id, library_item_id, order_index)
            VALUES (?, ?, ?)
        """, (playlist_id, library_item_id, next_order_index))
        db.commit()

        # Fetch the added song's details to return, aliasing pdf_url as file_path
        cursor.execute("SELECT id, name, type, pdf_url AS file_path FROM LibraryItem WHERE id = ?", (library_item_id,))
        added_song_info = cursor.fetchone()
        
        return jsonify({"message": "Song added to playlist", "song": dict(added_song_info)}), 201
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/playlists/<int:playlist_id>/songs/<int:song_id>', methods=['DELETE'])
def delete_song_from_playlist(playlist_id, song_id):
    """
    API endpoint to delete a song from a playlist.
    """
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute("DELETE FROM PlaylistSong WHERE playlist_id = ? AND library_item_id = ?", (playlist_id, song_id))
        if cursor.rowcount == 0:
            return jsonify({"error": "Song not found in this playlist"}), 404
        db.commit()
        return jsonify({"message": "Song removed from playlist successfully"}), 200
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/playlists/<int:playlist_id>/reorder', methods=['POST'])
def reorder_playlist_songs(playlist_id):
    """
    API endpoint to reorder songs within a playlist.
    Requires 'new_order' (list of library_item_ids in new sequence) in JSON body.
    """
    db = get_db()
    data = request.get_json()
    new_order_ids = data.get('new_order') # Expects a list of library_item_ids in their new order

    if not new_order_ids or not isinstance(new_order_ids, list):
        return jsonify({"error": "Invalid 'new_order' provided"}), 400

    try:
        cursor = db.cursor()
        # Validate that all IDs in new_order_ids actually belong to this playlist
        cursor.execute("SELECT library_item_id FROM PlaylistSong WHERE playlist_id = ?", (playlist_id,))
        existing_ids_in_playlist = {row['library_item_id'] for row in cursor.fetchall()}

        if not all(item_id in existing_ids_in_playlist for item_id in new_order_ids):
            return jsonify({"error": "One or more provided item IDs do not belong to this playlist"}), 400
        
        if len(new_order_ids) != len(existing_ids_in_playlist):
            return jsonify({"error": "New order list does not match current song count in playlist"}), 400


        # Update the order_index for each song
        for index, item_id in enumerate(new_order_ids):
            cursor.execute("UPDATE PlaylistSong SET order_index = ? WHERE playlist_id = ? AND library_item_id = ?",
                           (index, playlist_id, item_id))
        db.commit()
        return jsonify({"message": "Playlist songs reordered successfully"}), 200
    except sqlite3.Error as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/library/<int:item_id>/playlists', methods=['GET'])
def get_playlists_for_item(item_id):
    """
    API endpoint to fetch all playlists that a specific library item (song) belongs to.
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT p.id, p.name
        FROM Playlist p
        JOIN PlaylistSong ps ON p.id = ps.playlist_id
        WHERE ps.library_item_id = ?
        ORDER BY p.name ASC
    """, (item_id,))
    playlists = [dict(row) for row in cursor.fetchall()]
    return jsonify(playlists), 200


# --- Frontend Routes ---

# Set the root URL to serve library.html
@app.route('/')
def serve_library_default():
    """
    Serves the library.html page as the default route.
    """
    return send_from_directory('.', 'library.html')

# Define the route for the main PDF viewer page (index.html)
@app.route('/index.html')
def index():
    """
    Serves the index.html page, which is the PDF viewer.
    """
    return send_from_directory('.', 'index.html')

# Define the route for the library and playlist overview page (library.html)
@app.route('/library.html')
def library():
    """
    Serves the library.html page, which contains the library and playlist tabs.
    """
    return send_from_directory('.', 'library.html')

# Define the route for the playlist detail page (playlist.html)
@app.route('/playlist.html')
def playlist():
    """
    Serves the playlist.html page, displaying details of a specific playlist.
    """
    return send_from_directory('.', 'playlist.html')

# This block ensures the Flask development server runs only when the script is executed directly.
if __name__ == '__main__':
    app.run(debug=True)
