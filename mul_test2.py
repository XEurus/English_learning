import json
import spacy
from docx import Document
from docx.enum.text import WD_COLOR_INDEX
import multiprocessing
import re

def init_pool(shared_word_list, shared_word_info):
    global word_set
    global word_info_dict
    global nlp
    word_set = set(shared_word_list)
    word_info_dict = shared_word_info
    nlp = spacy.load('en_core_web_sm')

def extract_headwords_and_info_from_file(file_path):
    """
    从文件中逐行提取JSON数据，提取headWord字段和对应的信息。

    参数:
    file_path (str): 文件路径，文件包含多个JSON块。

    返回:
    tuple: (headwords, word_info_dict)
        - headwords: 包含所有提取的headWord的列表。
        - word_info_dict: 以headWord为键，词义和其他信息为值的字典。
    """
    headwords = []
    word_info_dict = {}

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                # 去除可能的空行和首尾空白
                line = line.strip()
                if line:  # 只有非空行才处理
                    try:
                        data = json.loads(line)  # 将每一行解析为JSON对象
                        head_word = data['headWord']
                        # 提取词义和其他信息（根据您的 JSON 结构调整）
                        content = data.get('content', {})
                        word_info = content.get('word', {}).get('content', {})
                        # 这里提取释义，可以根据实际情况调整
                        translation = word_info.get('trans', [])
                        trans_text = '; '.join([tran.get('tranCn', '') for tran in translation])
                        word_info_dict[head_word.lower()] = trans_text
                        headwords.append(head_word)
                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"Error processing line: {line}\nError: {e}")
                        continue  # 跳过错误的行

    except FileNotFoundError:
        print(f"File not found: {file_path}")

    return headwords, word_info_dict

def process_paragraph(paragraph_text):
    global word_set, word_info_dict, nlp
    doc = nlp(paragraph_text)
    new_runs = []
    found_words = set()
    sentence_end_positions = []
    for sent in doc.sents:
        sentence_end_positions.append(sent.end_char)

    token_positions = {}
    for token in doc:
        text = token.text_with_ws
        word = token.lemma_.lower()
        start_pos = token.idx
        end_pos = token.idx + len(token.text)
        token_positions[(start_pos, end_pos)] = token

    idx = 0
    while idx < len(doc.text):
        for (start_pos, end_pos), token in token_positions.items():
            if start_pos == idx:
                text = token.text_with_ws
                word = token.lemma_.lower()
                if word in word_set:
                    found_words.add(word)
                    run_info = {
                        'text': text,
                        'highlight': True,
                        'word': word,
                        'position': end_pos
                    }
                else:
                    run_info = {
                        'text': text,
                        'highlight': False,
                        'position': end_pos
                    }
                new_runs.append(run_info)
                idx += len(text)
                break
        else:
            # 未匹配到token，直接添加字符
            new_runs.append({
                'text': doc.text[idx],
                'highlight': False,
                'position': idx+1
            })
            idx += 1

    # 在句子末尾添加释义
    for i, run_info in enumerate(new_runs):
        if 'word' in run_info and run_info['highlight']:
            # 检查是否是句子的结尾
            if run_info['position'] in sentence_end_positions:
                # 添加释义
                meaning = word_info_dict.get(run_info['word'], '')
                if meaning:
                    # 创建一个新的 run，包含释义
                    new_runs.insert(i+1, {
                        'text': f' [{run_info["word"]}: {meaning}]',
                        'highlight': False
                    })

    return new_runs, found_words

def worker(paragraph_text):
    return process_paragraph(paragraph_text)

if __name__ == '__main__':
    # 加载单词列表和词义信息
    file_path = './CET6_2.json'  # 假设这是包含JSON数据的文件
    headwords, word_info_dict = extract_headwords_and_info_from_file(file_path)

    # 打印所有提取到的headWord
    for i, word in enumerate(headwords, 1):
        print(f"HeadWord {i}: {word}")

    # 单词列表
    word_list = headwords

    # 转换为小写，便于匹配
    word_list = [word.lower() for word in word_list]
    word_set = set(word_list)  # 在主进程中定义 word_set，便于后续计算

    # 加载文档
    document = Document('Stone.docx')
    print("document loaded")
    # 提取所有段落的文本
    paragraphs = [para.text for para in document.paragraphs]

    # 创建多进程池
    pool = multiprocessing.Pool(initializer=init_pool, initargs=(word_list, word_info_dict))

    # 并行处理段落
    results = pool.map(worker, paragraphs)

    pool.close()
    pool.join()

    # 创建新的文档
    new_document = Document()

    # 汇总所有找到的单词
    total_found_words = set()

    # 重新组装处理后的段落
    for result in results:
        runs, found_words = result  # 解包返回值
        total_found_words.update(found_words)  # 更新总的找到的单词集合
        para = new_document.add_paragraph()
        for run_info in runs:
            run = para.add_run(run_info['text'])
            if run_info.get('highlight', False):
                run.font.highlight_color = WD_COLOR_INDEX.YELLOW

    # 保存新的文档
    new_document.save('highlighted_document.docx')

    # 计算未找到的单词
    found_words_list = list(total_found_words)
    not_found_words = list(word_set - total_found_words)

    # 输出找到和未找到的单词数量
    print(f"找到的单词数量：{len(found_words_list)}")
    print(f"未找到的单词数量：{len(not_found_words)}")

    # 保存结果到文件
    with open('found_words.txt', 'w', encoding='utf-8') as f:
        for word in sorted(found_words_list):
            f.write(word + '\n')

    with open('not_found_words.txt', 'w', encoding='utf-8') as f:
        for word in sorted(not_found_words):
            f.write(word + '\n')
