#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工作目录创建工具

每次生成 PPT 前运行，确保工作目录存在。
目录命名格式：output/<主题名称>_<YYYYMMDD_HHMMSS>/

用法:
    python ensure_workdir.py <主题名称> [-r root_dir] [--output-dir dir_name]

示例:
    python ensure_workdir.py "AI 发展趋势"
    python ensure_workdir.py "技术汇报" -r /path/to/project
    python ensure_workdir.py "技术汇报" --output-dir dist

输出:
    打印创建的工作目录路径（供调用方使用）
"""

import os
import sys
import re
import argparse
from datetime import datetime
from pathlib import Path


def sanitize_topic(topic: str) -> str:
    """
    清理主题名称，移除不适合文件名的字符

    - 移除/、\、:、*、?、"、<、>、| 等非法字符
    - 空格转为下划线
    - 保留中文、英文、数字、下划线、中划线
    """
    # 移除非法字符
    sanitized = re.sub(r'[/\\:*?"<>|]', '', topic)
    # 空格转下划线
    sanitized = sanitized.replace(' ', '_')
    # 限制长度（避免路径过长）
    if len(sanitized) > 50:
        sanitized = sanitized[:50]
    return sanitized


def create_workdir(topic: str, base_dir: str = None, output_subdir: str = 'output') -> str:
    """
    创建工作目录

    Args:
        topic: PPT 主题名称
        base_dir: 基础目录，默认为项目根目录
        output_subdir: 输出目录名称（默认 'output'）

    Returns:
        创建的工作目录绝对路径
    """
    if base_dir is None:
        base_dir = os.getcwd()

    # 清理主题名称
    safe_topic = sanitize_topic(topic)

    # 生成时间戳
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 构建目录路径
    workdir_name = f"{safe_topic}_{timestamp}"
    workdir_path = os.path.join(base_dir, output_subdir, workdir_name)

    # 创建目录
    os.makedirs(workdir_path, exist_ok=True)

    return workdir_path


def main():
    parser = argparse.ArgumentParser(
        description='创建 PPT 工作目录',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
    python ensure_workdir.py "AI 发展趋势"
    python ensure_workdir.py "技术汇报" -r /path/to/project
    python ensure_workdir.py "技术汇报" --output-dir dist
'''
    )
    parser.add_argument('topic', type=str, help='PPT 主题名称（支持中文）')
    parser.add_argument('-r', '--root', type=str, default=None,
                        help='项目根目录（默认：自动推断）')
    parser.add_argument('--output-dir', type=str, default='output',
                        help='输出目录名称（默认: output）')

    args = parser.parse_args()

    # 确定基础目录
    if args.root:
        base_dir = args.root
    else:
        # 向上三级推断：svg_to_pptx/ -> scripts/ -> pptx-craft/ -> skills/
        script_dir = os.path.dirname(os.path.abspath(__file__))
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))

    try:
        workdir = create_workdir(args.topic, base_dir, args.output_dir)
        # 输出路径（供调用方捕获）
        print(workdir)
    except Exception as e:
        print(f"错误：创建目录失败 - {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
