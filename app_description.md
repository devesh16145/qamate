# Agrim ATS (Automated Testing System)

Agrim ATS is a professional, Windows-based automated testing platform designed for end-to-end (E2E) testing of the Agrim Seller App. It provides a user-friendly Electron desktop interface for managing and executing complex automated test suites without requiring direct interaction with Python or CLI commands.

## Key Features

- **100% Codeless Workflow**: Record new test cases directly in the browser and automatically save them into the system.
- **Data-Driven Testing**: Separation of test logic and test data. Configure test inputs (like search terms, quantities, etc.) via a simple UI form that updates a JSON backend.
- **Real-Time Live Logs**: View timestamped logs and execution progress directly within the desktop application.
- **Automated Evidence Collection**: Automatically captures video recordings of all tests and screenshots of any failures for easy debugging.
- **Professional Reporting**: Generates detailed Excel and JUnit XML reports for every test run, organized in timestamped folders.
- **Parallel Execution**: Supports running multiple tests simultaneously to significantly reduce total testing time.

## Technology Stack

- **Frontend**: Electron, HTML5, Vanilla CSS, JavaScript.
- **Backend Engine**: Python 3.12, Pytest, Playwright (Sync API).
- **Communication**: JSON-based IPC (Inter-Process Communication) between Node.js and Python.

## Getting Started

1. Ensure the Python virtual environment is set up in the root directory.
2. Run `run_ats.bat` from the root folder to launch the application.
3. Use the **Record Test** button to create new cases or select existing ones to **Run**.

---
*For technical implementation details and recent session history, refer to [ATS_Session_Summary_Apr_28.md](../ATS_Session_Summary_Apr_28.md).*
