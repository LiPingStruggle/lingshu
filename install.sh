#!/bin/bash
# 《灵枢》一键安装脚本 - 自动安装Python和依赖

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   《灵枢》(LingShu) 一键安装${NC}"
echo -e "${BLUE}   智能调度总控CLI工具${NC}"
echo -e "${BLUE}========================================${NC}"

detect_python() {
    for cmd in python3 python; do
        if command -v $cmd >/dev/null 2>&1; then
            version=$($cmd --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
            major=${version%.*}
            if [ "$major" -ge 3 ] 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(detect_python)
if [ -z "$PYTHON" ]; then
    echo -e "${YELLOW}⚠️  未检测到Python，正在安装...${NC}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install python
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get update && sudo apt-get install -y python3 python3-pip
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        echo -e "${RED}❌ Windows请从python.org下载安装Python${NC}"
        exit 1
    fi
    PYTHON=$(detect_python)
fi

echo -e "${GREEN}✅ 检测到: $($PYTHON --version)${NC}"

echo -e "\n${BLUE}📦 安装依赖包...${NC}"
$PYTHON -m pip install --upgrade pip -q
$PYTHON -m pip install litellm pyyaml rich -q

echo -e "\n${GREEN}✅ 安装完成！${NC}"
echo -e ""
echo -e "${BLUE}📖 使用方式:${NC}"
echo -e "  $PYTHON main.py run \"修复bug\""
echo -e "  $PYTHON main.py batch"
echo -e "  $PYTHON main.py smart \"分析问题\""
echo -e "  $PYTHON main.py --help"
