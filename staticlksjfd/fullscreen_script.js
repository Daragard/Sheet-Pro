// Select the body element to listen for taps
const body = document.body;
// Select the message box element
const messageBox = document.getElementById('messageBox');

// Function to display a message to the user
function showMessage(message, duration = 3000) {
    messageBox.textContent = message;
    messageBox.classList.remove('opacity-0');
    messageBox.classList.add('opacity-100');
    setTimeout(() => {
        messageBox.classList.remove('opacity-100');
        messageBox.classList.add('opacity-0');
    }, duration);
}

// Function to request fullscreen mode
function requestFullscreen() {
    // Check if fullscreen is supported by the browser
    if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen()
            .then(() => {
                // Successfully entered fullscreen
                showMessage("Entered fullscreen mode!");
            })
            .catch((err) => {
                // Error entering fullscreen
                showMessage(`Error entering fullscreen: ${err.message}`, 5000);
                console.error("Error attempting to enable full-screen mode:", err);
            });
    } else if (document.documentElement.webkitRequestFullscreen) { /* Safari */
        document.documentElement.webkitRequestFullscreen()
            .then(() => {
                showMessage("Entered fullscreen mode!");
            })
            .catch((err) => {
                showMessage(`Error entering fullscreen: ${err.message}`, 5000);
                console.error("Error attempting to enable full-screen mode (webkit):", err);
            });
    } else if (document.documentElement.msRequestFullscreen) { /* IE11 */
        document.documentElement.msRequestFullscreen()
            .then(() => {
                showMessage("Entered fullscreen mode!");
            })
            .catch((err) => {
                showMessage(`Error entering fullscreen: ${err.message}`, 5000);
                console.error("Error attempting to enable full-screen mode (ms):", err);
            });
    } else {
        // Fullscreen API is not supported
        showMessage("Fullscreen mode is not supported by your browser.", 5000);
    }
}

// Add a click event listener to the entire body to trigger fullscreen
body.addEventListener('click', requestFullscreen);

// Optional: Listen for fullscreen change events to update UI or state
document.addEventListener('fullscreenchange', () => {
    if (document.fullscreenElement) {
        // The document is now in fullscreen mode
        console.log("Document entered fullscreen mode.");
    } else {
        // The document exited fullscreen mode
        console.log("Document exited fullscreen mode.");
    }
});
document.addEventListener('webkitfullscreenchange', () => {
    if (document.webkitFullscreenElement) {
        console.log("Document entered webkit fullscreen mode.");
    } else {
        console.log("Document exited webkit fullscreen mode.");
    }
});
document.addEventListener('msfullscreenchange', () => {
    if (document.msFullscreenElement) {
        console.log("Document entered ms fullscreen mode.");
    } else {
        console.log("Document exited ms fullscreen mode.");
    }
});