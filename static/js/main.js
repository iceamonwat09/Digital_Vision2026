// Main JavaScript file for common functionality

// Add any common JavaScript functions here
// Currently, page-specific JavaScript is in template files

console.log('Defect Detection System loaded');

// Utility function for formatting timestamps
function formatTimestamp(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleString();
}

// Utility function for handling API errors
function handleApiError(error, message) {
    console.error(message, error);
    // Could show a toast notification here
}
