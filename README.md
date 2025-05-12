<div align="center">
    <h1>115Bot - Telegram 机器人</h1>
    <p>简体中文 | <a href="./README_EN.md">[English]</a> </p>
</div>

一个基于 Python 的 Telegram 机器人，用于管理和控制 115 网盘，支持离线下载、视频上传、目录同步等功能。

## 更新日志
v2.3.0
- 增加电影订阅功能
- 修复了部分bug

v2.2.0
- 修复了部分bug

## 上游项目
本项目基于以下项目开发，特此鸣谢

ChenyangGao/web-mount-packs/python-115-client

## 项目背景
本项目源于个人日常观影体验的优化需求。作为一个影视爱好者，我采用 115网盘 + CloudDrive2 + Emby 的组合方案来管理和观看媒体内容。

想象这样一个场景：

在通勤路上刷到一部令人心动的电影，只需将找到的磁力链接随手发给 TG 机器人，它就会：
- 自动将影片离线下载到 115 网盘的指定分类目录
- 智能清理各类广告文件
- 自动创建 STRM 文件并通知 Emby 进行媒体库刮削

当你结束一天的工作回到家，只需准备好零食饮品，打开 Emby 就能享受精心整理好的观影体验。让一部好电影为你洗去一天的疲惫，享受属于自己的放松时光。

## 已知缺陷
- 目前对剧集的支持度非常有限，如果直接离线剧集资源可能会发生意想不到的问题
- 同步目录会清空整个文件夹，包括元数据，相当粗暴

如果你乐意帮助改进这个项目欢迎[加入](https://t.me/qiqiandfei)！

## 功能特性

- 🔐 **115 账号管理**
  - Cookie 设置与验证
  - 账号状态监控

- 📥 **离线下载**
  - 支持多种下载协议：磁力链接、115分享链接、迅雷、ed2k、FTP、HTTPS
  - 自动分类存储
  - 广告文件自动清理
  - strm自动创建

- 🔄 **目录同步**
  - 自动创建本地软链接
  - STRM 文件生成
  - Emby 媒体库集成

- 📺 **视频处理**
  - 视频文件自动上传至 115 (会消耗机场/VPS流量，慎用)

- 📡 **订阅功能**
  - AV女优订阅
  - 电影订阅
  - 自定义存储路径

## 快速开始

### 环境要求

- Docker 环境
- Python 3.12+
- 可访问的 Telegram 网络环境

### 安装部署

1. **克隆项目**
   ```bash
   git clone https://github.com/qiqiandfei/Telegram-115bot.git
   cd 115bot
   ```

2. **配置文件设置**
   - 复制配置文件模板
     ```bash
     cp config/config.yaml.example config/config.yaml
     ```
   - 编辑 `config.yaml`，填入必要配置：
     - Telegram Bot Token
     - 授权用户列表
     - 115网盘配置
     - 目录映射设置

3. **Docker部署**
   
   **本地**
   ```bash
   # 构建基础镜像
   docker build -t 115bot:base -f Dockerfile.base .
   
   # 构建应用镜像
   docker build -t 115bot:latest .
   
   # 运行容器
   docker run -d \
     --name tg-bot-115 \
     --restart unless-stopped \
     -e TZ=Asia/Shanghai \
     -v $PWD/config:/config \
     -v /path/to/media:/media \
     -v /path/to/CloudNAS:/CloudNAS:rslave \
     115bot:latest
   ```
   
   **Compose（推荐）**
   ```
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
        - /path/to/media:/media # Emby媒体库目录（软链目录）
        - /path/to/CloudNAS:/CloudNAS:rslave # CloudDrive2挂载目录
   ```

## 配置说明

请参考 `config/config.yaml.example` 文件中的注释进行配置。

### 目录结构
```
115bot/
├── app/              # 应用源码
├── config/           # 配置文件
│   ├── config.yaml   # 主配置文件
│   ├── cookie.txt    # 115网盘Cookie
│   └── db.db         # SQLite数据库
├── tmp/              # 临时文件目录
├── images/           # 图片资源目录
├── Dockerfile        # 应用 Dockerfile
├── Dockerfile.base   # 基础镜像 Dockerfile
└── requirements.txt  # Python 依赖
```

## 使用指南

### 基本命令

- `/start`   - 显示帮助信息
- `/cookie`  - 设置 115 Cookie
- `/dl`      - 添加离线下载
- `/sync`    - 同步目录并创建软链（会删除当前目录下所有文件，大规模同步可能导致风控，慎用！）
- `/sm`      - 订阅电影
- `/sub`     - 女优订阅
- `/q`       - 取消当前会话

### 注意事项

1. 首次使用需要设置 115 Cookie [获取115cookie](https://greasyfork.org/zh-CN/scripts/474231-115%E4%B8%8D%E5%A4%A7%E5%8A%A9%E6%89%8B-full)
2. 离线下载支持多种格式，直接发送链接即可
3. 同步目录时会清空对应的 STRM 目录
4. 订阅功能会自动创建以演员命名的文件夹


### 许可证
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
![请我喝咖啡](https://alist.qiqiandfei.fun:8843/d/Syncthing/yufei/%E4%B8%AA%E4%BA%BA/%E8%B5%9E%E8%B5%8F.png)