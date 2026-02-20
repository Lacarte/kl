"Design and implement a high-performance Python console-based keylogger for Windows, with the following advanced features:

Track Open Windows: Continuously monitor and list all active Windows applications (e.g., "Open Window: Notepad", "Open Window: Chrome").
Real-Time Keystroke Capture: Stream every keystroke (including Enter) in real-time, with no raw key codes (only human-readable text like "Enter key" or "Clicked button").
Filter Non-Printable Characters: Exclude non-printable characters (e.g., \n, \t, \r) and log only meaningful text (e.g., "Keystroke: Hello" instead of raw ASCII codes).
Efficient Processing: Optimize for minimal memory usage and fast input handling (e.g., process keystrokes in chunks, avoid storing all input).
Contextual Window Names: Display the name of the currently open application (e.g., "Open Window: Notepad") alongside each keystroke for clarity.
Keyboard Shortcut Detection: Identify and log common shortcuts (e.g., "Ctrl+C", "Ctrl+V") as "Keystroke: [Shortcut]".
User Interface (Optional): Add a simple console-based UI to show active windows and the keystroke log (e.g., a list of recent entries).
Error Handling: Gracefully handle permission issues (e.g., "Access denied for 'Notepad'") and log errors without crashing.
Memory Efficiency: Use a circular buffer to store recent keystrokes (e.g., last 100 entries) to reduce memory footprint.
Output Formatting: Format the log with timestamps and clear labels (e.g., "Enter key" instead of raw codes) for readability.
Example Requirements:

The program should not display raw key codes (e.g., 13 for Enter).
Ensure the log is user-friendly and avoids clutter (e.g., no separate entries for each keystroke).
Bonus Challenge: How do you balance real-time performance with accurate logging, especially under high input pressure?

Your Goal: Build a robust, efficient, and user-friendly keylogger that meets all requirements while demonstrating strong problem-solving skills.

