import argparse
import re
import sys
from collections import Counter
from typing import List, Dict, Tuple

def parse_arguments():
    parser = argparse.ArgumentParser(description="日志分析器 (Log Analyzer)")
    parser.add_argument("file_path", help="要分析的日志文件路径")
    return parser.parse_args()

def analyze_log(file_path: str) -> Tuple[Dict[str, int], List[str]]:
    stats = Counter()
    errors = []
    
    # 正则表达式匹配日志行：时间戳（可选）、日志级别、消息内容
    # 兼容 INFO: message 或 [2023-...] INFO: message
    pattern = re.compile(r'^(?P<timestamp>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2})?\s*(?P<level>\w+):\s*(?P<message>.*)$')
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                match = pattern.match(line)
                if match:
                    level = match.group('level').upper()
                    stats[level] += 1
                    
                    if level == 'ERROR':
                        errors.append(line)
        
        return stats, errors
    except FileNotFoundError:
        print(f"错误: 找不到文件 {file_path}")
        sys.exit(1)
    except Exception as e:
        print(f"发生错误: {e}")
        sys.exit(1)

def generate_report(file_path: str, stats: Dict[str, int], errors: List[str]):
    print("=== 日志分析简报 ===")
    print(f"分析文件: {file_path}")
    print("-" * 20)
    
    if not stats:
        print("未发现有效日志条目。")
    else:
        print("统计摘要:")
        # 按出现次数排序或按级别顺序
        for level, count in stats.items():
            print(f"- {level}: {count}")
            
    print("-" * 20)
    
    if errors:
        print("错误详情:")
        for idx, error in enumerate(errors, 1):
            print(f"{idx}. {error}")
    else:
        print("未发现错误。")
        
    print("=" * 20)

def main():
    args = parse_arguments()
    stats, errors = analyze_log(args.file_path)
    generate_report(args.file_path, stats, errors)

if __name__ == "__main__":
    main()
