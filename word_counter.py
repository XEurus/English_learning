import re
import os
import sys
import random
from collections import defaultdict

def read_words_from_file(word_file_path):
    """从文本文件中读取单词，每行一个单词"""
    try:
        with open(word_file_path, 'r', encoding='utf-8') as file:
            # 去除空白并过滤空行
            words = [line.strip() for line in file if line.strip()]
        return words
    except Exception as e:
        print(f"读取单词文件时出错: {e}")
        return []

def read_text_from_markdown(markdown_file_path):
    """从markdown文件中读取文本内容"""
    try:
        with open(markdown_file_path, 'r', encoding='utf-8') as file:
            content = file.read()
        return content
    except Exception as e:
        print(f"读取markdown文件时出错: {e}")
        return ""

def check_words_in_text(words, text):
    """检查哪些单词在文本中出现，哪些没有出现"""
    found_words = []
    not_found_words = []
    
    # 将文本转换为小写以进行不区分大小写的匹配
    text_lower = text.lower()
    
    for word in words:
        word_lower = word.lower()
        # 使用单词边界确保我们匹配的是完整单词
        pattern = r'\b' + re.escape(word_lower) + r'\b'
        if re.search(pattern, text_lower):
            found_words.append(word)
        else:
            not_found_words.append(word)
    
    return found_words, not_found_words

def save_results_to_file(found_words, not_found_words, output_dir=None):
    """将结果保存到文件"""
    try:
        # 如果提供了输出目录，则在该目录中创建文件
        if output_dir:
            found_words_path = os.path.join(output_dir, 'found_words.txt')
            not_found_words_path = os.path.join(output_dir, 'not_found_words.txt')
            # 确保目录存在
            os.makedirs(output_dir, exist_ok=True)
        else:
            found_words_path = 'found_words.txt'
            not_found_words_path = 'not_found_words.txt'
        
        with open(found_words_path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(found_words))
        
        with open(not_found_words_path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(not_found_words))
        
        print(f"结果已保存到 '{found_words_path}' 和 '{not_found_words_path}'")
    except Exception as e:
        print(f"保存结果时出错: {e}")

def shuffle_words_and_group(input_file_path, output_file_path, group_size=20):
    """
    读取文本文件中的单词，随机打乱顺序，然后按指定大小分组输出到新文件，
    每组之间插入一行空白
    """
    try:
        # 读取单词
        words = read_words_from_file(input_file_path)
        
        # 随机打乱单词顺序
        random.shuffle(words)
        
        # 按指定大小分组
        grouped_words = []
        for i in range(0, len(words), group_size):
            group = words[i:i+group_size]
            grouped_words.append('\n'.join(group))
        
        # 确保输出目录存在
        dirname = os.path.dirname(output_file_path)
        if dirname:  # 只有当目录名不为空时才创建目录
            os.makedirs(dirname, exist_ok=True)
        
        # 写入到新文件，每组之间插入一行空白
        with open(output_file_path, 'w', encoding='utf-8') as file:
            file.write('\n\n'.join(grouped_words))
        
        print(f"已将打乱并分组的单词保存到 '{output_file_path}'")
        return True
    except Exception as e:
        print(f"打乱并分组单词时出错: {e}")
        return False

def counter(word_file_path, markdown_file_path, output_dir=None):
    """
    计算单词在文本中的出现情况
    
    参数:
    word_file_path: 单词文件路径
    markdown_file_path: 文本文件路径
    output_dir: 输出目录，如果提供则将结果保存在该目录中
    """
    # 检查文件是否存在
    if not os.path.exists(word_file_path):
        print(f"单词文件 '{word_file_path}' 未找到。")
        return
    
    if not os.path.exists(markdown_file_path):
        print(f"文本文件 '{markdown_file_path}' 未找到。")
        return
    
    # 读取单词和文本
    words = read_words_from_file(word_file_path)
    text = read_text_from_markdown(markdown_file_path)
    
    # 检查单词在文本中的出现情况
    found_words, not_found_words = check_words_in_text(words, text)
    
    # 打印摘要
    print(f"检查的单词总数: {len(words)}")
    print(f"找到的单词数: {len(found_words)}")
    print(f"未找到的单词数: {len(not_found_words)}")
    
    # 保存结果到文件
    save_results_to_file(found_words, not_found_words, output_dir)
    
    # # 打印一些示例
    # if found_words:
    #     print("\n找到的单词示例:")
    #     for word in found_words:  # 显示最多10个示例
    #         print(f"- {word}")
    
    # if not_found_words:
    #     print("\n未找到的单词示例:")
    #     for word in not_found_words:  # 显示最多10个示例
    #         print(f"- {word}")

if __name__ == "__main__":
    # 如果命令行参数指定了 "shuffle"，则执行打乱并分组的功能
    if len(sys.argv) > 1 and sys.argv[1] == "shuffle":
        input_file = 'u1.txt'  # 默认输入文件
        output_file = 'shuffled_words.txt'  # 默认输出文件
        group_size = 5  # 默认分组大小
        
        # 允许自定义参数
        if len(sys.argv) > 2:
            input_file = sys.argv[2]
        if len(sys.argv) > 3:
            output_file = sys.argv[3]
        if len(sys.argv) > 4:
            try:
                group_size = int(sys.argv[4])
            except ValueError:
                print(f"分组大小必须是整数，将使用默认值 {group_size}")
        
        shuffle_words_and_group(input_file, output_file, group_size)
    else:
        # 默认文件路径
        word_file_path = 'u1.txt'
        markdown_file_path = 'generated_story.txt'
        
        # 允许通过命令行参数指定不同的文件
        if len(sys.argv) > 1:
            word_file_path = sys.argv[1]
        if len(sys.argv) > 2:
            markdown_file_path = sys.argv[2]
        if len(sys.argv) > 3:
            output_dir = sys.argv[3]
        else:
            output_dir = os.path.dirname(markdown_file_path)
            
        counter(word_file_path, markdown_file_path, output_dir)
