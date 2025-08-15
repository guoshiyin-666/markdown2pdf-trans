# 使用官方 Python 3.12.2 基础镜像
FROM python:3.12.2

# 替换为清华 Debian APT 源（适配 Debian 12 / bookworm 的 debian.sources）
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources

# 可选：输出验证源是否替换成功
# RUN cat /etc/apt/sources.list.d/debian.sources

# 设置 pip 使用清华 PyPI 源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 Python 库
RUN pip install flask pandas pypdf pypandoc weasyprint requests

# 安装 pandoc 和 XeLaTeX 中文支持
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        pandoc \
        texlive-xetex \
        texlive-lang-chinese \
        fonts-noto-cjk && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 拷贝项目代码
COPY . /app

# 设置编码
ENV PYTHONIOENCODING=UTF-8

# 暴露端口
EXPOSE 5000

# 启动服务
CMD ["python", "ai_translator"]
