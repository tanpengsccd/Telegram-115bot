FROM 115bot:base
LABEL authors="qiqiandfei"

# 设置工作目录
WORKDIR /app

# 复制app下所有文件到/app
ADD ./app .

ENV PYTHONPATH="/app"

CMD ["python", "115bot.py"]

