# 多阶段构建——同 Go 组件的既有模式（build 阶段带 git/编译工具链，运行
# 阶段只留运行时真正要用的东西）：``besdk`` 是 git+https 依赖
# （pyproject.toml），``pip install`` 时需要 git 二进制去 clone，真机
# build 时报过 "Cannot find command 'git'"——git/build-essential 只留在
# build 阶段，不进最终镜像。
FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
COPY gen ./gen

RUN pip install --no-cache-dir --prefix=/install .

# python:3.12-slim——不是总纲 SOP-B 旧版写的 3.11（be-sdk-python 要求
# >=3.12，见其 pyproject.toml）。基底必须带 shell（导读第 8 条：平台的
# 健康检查是 CMD-SHELL + wget，distroless/scratch 会让"已就绪"永远
# 探测不到）。
FROM python:3.12-slim

# 真机验证过的 WeasyPrint 系统依赖（一次性容器实测，见
# docs/design/infra-print.md 的 §8 参考实现记录）：只装
# libpango/libcairo/libgdk-pixbuf 不装字体时，中文 PDF 只嵌 DejaVu，
# pdftotext 提取出来是乱码且重复；补 fonts-noto-cjk + fontconfig +
# fc-cache -f 之后干净。wget 是健康检查必需（导读第 8 条）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libgdk-pixbuf-2.0-0 libcairo2 \
    fontconfig fonts-noto-cjk \
    wget ca-certificates tzdata \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# build 阶段与运行阶段是同一个基底镜像（同 python:3.12-slim），
# --prefix=/install 装出来的 site-packages 路径跟这里的解释器一致，
# 直接搬到 /usr/local 就在默认 sys.path 上，不需要额外设 PYTHONPATH。
COPY --from=build /install /usr/local

WORKDIR /app
COPY migrations ./migrations
COPY component.yaml ./

EXPOSE 8400 9400

ENTRYPOINT ["python", "-m", "app.main"]
