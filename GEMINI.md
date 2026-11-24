
# Project Overview

This project is a Python-based Telegram bot for managing a 115.com account. It allows users to interact with their 115.com cloud storage through a Telegram interface, enabling features such as offline downloading, video uploading, and directory synchronization. The bot is designed to be deployed using Docker and can be configured via a `config.yaml` file.

## Key Technologies

*   **Backend:** Python
*   **Telegram Bot Framework:** `python-telegram-bot`
*   **Dependencies:** `alibabacloud_oss_v2`, `APScheduler`, `beautifulsoup4`, `playwright`, `PyYAML`, `qrcode`, `Requests`, `Telethon`, `urllib3`, `Pillow`, `bs4`, `aria2p`, `nest-asyncio`
*   **Deployment:** Docker

## Architecture

The application is structured into several directories:

*   `app/`: Contains the main application logic, including the bot's entry point (`115bot.py`), core functionalities, Telegram handlers, and utility scripts.
*   `config/`: Holds configuration files, including a sample `config.yaml.example`.
*   `legacy/`: Contains older, possibly deprecated code.
*   `tmp/`: A temporary directory for transient files.

The bot's functionality is modularized into different handlers for authentication, downloading, synchronization, and other features. It uses a message queue to handle asynchronous tasks and a scheduler for periodic jobs.

# Building and Running

## Docker (Recommended)

The recommended way to run the bot is using Docker and Docker Compose.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/qiqiandfei/Telegram-115bot.git
    cd Telegram-115bot
    ```

2.  **Configure the bot:**
    *   Copy the example configuration file: `cp config/config.yaml.example config/config.yaml`
    *   Edit `config/config.yaml` with your Telegram bot token, 115.com credentials, and other settings.

3.  **Run with Docker Compose:**
    ```bash
    docker-compose up -d
    ```

## Local Development

To run the bot locally for development:

1.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure the bot:**
    *   Copy the example configuration file: `cp config/config.yaml.example config/config.yaml`
    *   Edit `config/config.yaml` with your Telegram bot token, 115.com credentials, and other settings.

3.  **Run the bot:**
    ```bash
    python app/115bot.py
    ```

# Development Conventions

*   **Coding Style:** The code follows standard Python conventions (PEP 8).
*   **Configuration:** Application settings are managed through a `config.yaml` file.
*   **Modularity:** The code is organized into modules and handlers for different functionalities.
*   **Asynchronous Operations:** The bot uses `asyncio` and a message queue to handle long-running tasks without blocking the main thread.
*   **Error Handling:** The code includes error handling and logging to ensure robustness.
