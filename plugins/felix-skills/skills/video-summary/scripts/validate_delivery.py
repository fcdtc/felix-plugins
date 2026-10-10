#!/usr/bin/env python3
"""验证 video-summary 的两份最终交付文件。"""
import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = '<!-- 总结内容将由后续流程生成 -->'


def error(code, message):
    return {'schema_version': '1.0', 'ok': False,
            'error': {'code': code, 'message': message}}


def read_markdown(path, missing_code, invalid_code):
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        return None, candidate, error(missing_code, f'文件不存在: {candidate}')
    try:
        return candidate.read_text(encoding='utf-8'), candidate, None
    except UnicodeDecodeError:
        return None, candidate, error(invalid_code, f'文件不是有效 UTF-8: {candidate}')


def visible_body(markdown):
    lines = markdown.splitlines()
    body = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence or line.startswith('#') or line.startswith('> ') \
                or line.strip() == '---' or line.strip().startswith('<!--'):
            continue
        body.append(line)
    text = '\n'.join(body)
    text = re.sub(r'`[^`]*`', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    return text.strip()


def validate(verbatim_path, summary_path):
    verbatim, verbatim_file, problem = read_markdown(
        verbatim_path, 'missing_verbatim', 'invalid_verbatim_encoding')
    if problem:
        return problem
    summary, summary_file, problem = read_markdown(
        summary_path, 'missing_summary', 'invalid_summary_encoding')
    if problem:
        return problem
    if verbatim_file == summary_file:
        return error('duplicate_delivery_path', '逐字稿和总结稿不能是同一个文件')
    if not verbatim.startswith('# '):
        return error('invalid_verbatim_title', '逐字稿缺少一级标题')
    if not visible_body(verbatim):
        return error('empty_verbatim', '逐字稿正文为空')
    if not summary.startswith('# '):
        return error('invalid_summary_title', '总结稿缺少一级标题')
    if PLACEHOLDER in summary:
        return error('summary_placeholder_present', '总结稿仍包含占位内容')
    body = visible_body(summary)
    visible = re.sub(r'\s+', '', body)
    if len(visible) < 200:
        return error('summary_too_short', f'总结正文过短: {len(visible)} < 200')
    chinese = len(re.findall(r'[一-鿿]', visible))
    if chinese < 100 or chinese / max(len(visible), 1) < 0.5:
        return error('summary_not_chinese', '总结稿缺少足够的中文内容')
    return {
        'schema_version': '1.0',
        'ok': True,
        'deliverables': {'verbatim': str(verbatim_file), 'summary': str(summary_file)},
        'metrics': {'verbatim_chars': len(visible_body(verbatim)),
                    'summary_chars': len(visible), 'summary_chinese_chars': chinese},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbatim', required=True)
    parser.add_argument('--summary', required=True)
    args = parser.parse_args()
    payload = validate(args.verbatim, args.summary)
    print('DELIVERY_JSON:' + json.dumps(payload, ensure_ascii=False))
    return 0 if payload['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
