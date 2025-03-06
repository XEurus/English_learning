import spacy
from docx import Document
from docx.shared import RGBColor
import json
# 加载英语模型
nlp = spacy.load("en_core_web_sm")

def get_word_forms(base_word):
    """使用spaCy获取单词的各种形态"""
    doc = nlp(base_word)
    forms = set()
    for token in doc:
        forms.add(token.text.lower()) # 原形
        forms.add(token.lemma_.lower()) # 基本形式
    return forms

def highlight_word_in_paragraph(paragraph, word_forms):
    """在段落中高亮显示指定的单词形态"""
    for run in paragraph.runs:
        # 分割单词并保留分隔符
        words = spacy.util.split_words_with_spaces(run.text)
        new_text = ''
        for word in words:
            clean_word = word.text.strip('.,!?;:"()').lower()
            if clean_word in word_forms:
                # 创建一个带有高亮的新run
                new_run = paragraph.add_run(word.text_with_ws, style=run.style)
                new_run.font.highlight_color = RGBColor(255, 255, 0) # 黄色高亮
            else:
                new_text += word.text_with_ws
        run.text = new_text

def process_document(document, base_word):
    """处理文档，搜索并高亮单词及其变形"""

    word_forms = get_word_forms(base_word)

    # 遍历文档中的每个段落
    for paragraph in document.paragraphs:
        highlight_word_in_paragraph(paragraph, word_forms)

    # 保存修改后的文档
    document.save('highlighte_ddocument.docx')


def extract_headwords_from_file(file_path):
    """
    从文件中逐行提取JSON数据，并从每个JSON块中提取headWord字段。

    参数:
    file_path (str): 文件路径，文件包含多个JSON块。

    返回:
    list: 包含所有提取的headWord的列表。
    """
    headwords = []

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                # 去除可能的空行和首尾空白
                line = line.strip()
                if line:  # 只有非空行才处理
                    try:
                        data = json.loads(line)  # 将每一行解析为JSON对象
                        head_word = data['content']['word']['wordHead']
                        headwords.append(head_word)
                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"Error processing line: {line}\nError: {e}")
                        continue  # 跳过错误的行

    except FileNotFoundError:
        print(f"File not found: {file_path}")

    return headwords

def main():
    # 获取单词
    file_path = './CET6_2.json'  # 假设这是包含JSON数据的文件
    headwords = extract_headwords_from_file(file_path)

    doc_path = 'Stone.doc'  # 指定文档路径
    document = Document(doc_path)
    #base_word = 'run'  # 指定要搜索的单词
    # 打印所有提取到的headWord
    for i, word in enumerate(headwords, 1):
        print(f"HeadWord {i}: {word}")
        process_document(document, word)


if __name__ == "__main__":
    main()