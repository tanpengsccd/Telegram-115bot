#!/bin/bash

# 构建基础镜像
echo "Building base image..."
docker build -f Dockerfile.base -t 115bot:base .

# 检查基础镜像构建结果
if [ $? -ne 0 ]; then
    echo "Base image build failed!"
    exit 1
fi

# 构建应用镜像
echo "Building application image..."
docker build -f Dockerfile -t 115bot:latest .

# 检查应用镜像构建结果
if [ $? -ne 0 ]; then
    echo "Application image build failed!"
    exit 1
fi

echo "Build completed successfully!"

# 显示镜像大小
echo "Image sizes:"
docker images | grep 115bot
