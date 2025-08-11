<div align="center">
    <h1>115Bot - Telegram Bot</h1>
    <p>English | <a href="./README.md">[简体中文]</a></p>
</div>

A Python-based Telegram bot for managing and controlling 115 Network Disk, supporting offline downloads, video uploads, directory synchronization, and more.

## Update Log
v3.0.0
- Refactored underlying interface, all 115 requests now use the open platform API for faster and more stable performance!
- Optimized video file upload, supporting large video uploads
- Temporarily disabled AV subscription feature, will update when a stable and reliable interface is found

v2.3.7
- Fixed the bug where subscription wasn't automatically cancelled after manually downloading subscribed movies

v2.3.6
- Fixed error with subscribe movie download
- Due to the triggering of JAVDB's anti-crawling mechanism, the crawling failed and the AV subscription function was temporarily closed. A stable solution will be updated later.

v2.3.5
- Fixed escaping errors in MarkdownV2 formatting for offline download timeout alert messages  
- Optimized movie subscription download logic  

v2.3.4
- bug fix

v2.3.3
- bug fix

v2.3.2
- bug fix

v2.3.1
- When the actress subscription offline download failed, bot will try to change the magnet link

v2.3.0
- Added movie subscription feature
- Fixed various bugs

v2.2.0
- Fixed various bugs

## Background
This project originated from the need to optimize personal daily viewing experience. As a movie enthusiast, I use the combination of 115 Network Disk + CloudDrive2 + Emby to manage and watch media content.

Imagine this scenario:

While commuting, you come across an interesting movie. Simply send the magnet link to the TG bot, and it will:
- Automatically download the movie to the specified category directory in 115 Network Disk
- Intelligently clean up advertisement files
- Automatically create STRM files and notify Emby for media library scanning

When you return home after work, just prepare some snacks and drinks, open Emby, and enjoy a well-organized viewing experience. Let a good movie wash away your daily fatigue and help you relax.

## Known Issues
- Limited support for TV series. Downloading series directly may cause unexpected issues
- Directory synchronization will clear the entire folder, including metadata (quite aggressive)

If you'd like to help improve this project, welcome to [join](https://t.me/qiqiandfei)!

## Features

- 🔐 **115 Account Management**
  - Cookie setup and verification
  - Account status monitoring

- 📥 **Offline Download**
  - Support multiple download protocols: Magnet links, 115 share links, Thunder, ed2k, FTP, HTTPS
  - Automatic category storage
  - Advertisement file cleanup
  - Automatic STRM creation

- 🔄 **Directory Synchronization**
  - Automatic local symlink creation
  - STRM file generation
  - Emby media library integration

- 📺 **Video Processing**
  - Automatic video upload to 115 (caution: consumes VPS bandwidth)

## Quick Start

### Requirements

- Docker environment
- Python 3.12+
- Accessible Telegram network environment

### Installation

1. **Clone Project**
   ```bash
   git clone https://github.com/qiqiandfei/Telegram-115bot.git
   cd 115bot
   ```

2. **Configure Settings**
   - Copy configuration template
     ```bash
     cp config/config.yaml.example config/config.yaml
     ```
   - Edit `config.yaml`, fill in necessary configurations:
     - Telegram Bot Token
     - Authorized user list
     - 115 Network Disk configuration
     - Directory mapping settings

3. **Docker Deployment**

   **Local**
   ```bash
   # Build base image
   docker build -t 115bot:base -f Dockerfile.base .
   
   # Build application image
   docker build -t 115bot:latest .
   
   # Run container
   docker run -d \
     --name tg-bot-115 \
     --restart unless-stopped \
     -e TZ=Asia/Shanghai \
     -v $PWD/config:/config \
     -v /path/to/media:/media \
     -v /path/to/CloudNAS:/CloudNAS:rslave \
     115bot:latest
   ```
   
   **Compose (recommended)**
   ```yaml
   # docker-compose.yaml
   version: '3.8'
   services:
     115-bot:
       container_name: tg-bot-115
       environment:
         TZ: Asia/Shanghai
       image: qiqiandfei/115-bot:latest
       # privileged: True
       restart: unless-stopped
       volumes:
         - $PWD/config:/config
         - /path/to/media:/media # Emby media library directory (symlink directory)
         - /path/to/CloudNAS:/CloudNAS:rslave # CloudDrive2 mount directory
   ```

## Configuration

Please refer to the comments in `config/config.yaml.example` for configuration details.

### Directory Structure
```
115bot/
├── app/              # Application source code
├── config/           # Configuration files
│   ├── config.yaml   # Main configuration
│   ├── cookie.txt    # 115 Network Disk Cookie
│   └── db.db         # SQLite database
├── tmp/              # Temporary files
├── images/           # Image resources
├── Dockerfile        # Application Dockerfile
├── Dockerfile.base   # Base image Dockerfile
└── requirements.txt  # Python dependencies
```

## Usage Guide

### Basic Commands

- `/start`   - Show help information
- `/cookie`  - Set 115 Cookie
- `/dl`      - Add offline download
- `/sync`    - Sync directory and create symlinks (will delete all files in current directory, use with caution!)
- `/sm`      - Subscribe to movies
- `/q`       - Cancel current session

### 115 Open Platform Application

It's recommended to apply for the 115 Open Platform for a better experience. Application URL: [115 Open Platform](https://open.115.com/)
After approval, fill in the 115_app_id in the configuration file.

If you don't want to use the 115 Open Platform, please use the previous image version: qiqiandfei/115-bot:v2.3.7

### Video Download Instructions

Due to Telegram bot API limitations, videos larger than 20MB cannot be downloaded. Therefore, if you need video download functionality, you need to use the Telegram client.

Steps to get Telegram client session file:
  - 1. Log in to Telegram platform and create a personal app application [https://my.telegram.org/auth](https://my.telegram.org/auth)
  - 2. Get app_id and app_hash
  - 3. Fill in the id and hash obtained in step 2 into the create_tg_session_file.py script
  - 4. Execute the create_tg_session_file script to get the "user_session.session" file
  - 5. Put "user_session.session" in the config directory and mount it to the container

If you don't perform this step, it won't affect the 115-bot operation, you just won't be able to download video files larger than 20MB.

### License
```
MIT License

Copyright (c) 2024 Fei

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software...
```

## Buy me a coffee~
![Buy me a coffee](https://alist.qiqiandfei.fun:8843/d/Syncthing/yufei/%E4%B8%AA%E4%BA%BA/%E8%B5%9E%E8%B5%8F.png)
