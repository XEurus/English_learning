import docx
from nltk import WordNetLemmatizer
from nltk.corpus import wordnet
import nltk
import re
import json
import multiprocessing


def get_word_forms(word):
    lemmatizer = WordNetLemmatizer()
    forms = set()
    forms.add(word)
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.VERB))
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.NOUN))
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.ADJ))
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.ADV))
    return forms


def process_paragraph(paragraph_text, words):
    found_words = []
    not_found_words = list(words)
    new_text = paragraph_text

    for word in words:
        print("working at:",word)
        word_forms = get_word_forms(word)
        last_pos = 0
        for form in word_forms:
            # 使用正则表达式确保匹配完整单词
            pattern = r'\b' + re.escape(form) + r'\b'
            while True:
                match = re.search(pattern, new_text, re.IGNORECASE)
                if not match:
                    break
                start, end = match.span()
                new_text = new_text[:start] + "<highlight>" + new_text[start:end] + "</highlight>" + new_text[end:]
                found_words.append(word)
                if word in not_found_words:
                    not_found_words.remove(word)

    return new_text, found_words, not_found_words


def highlight_words_in_doc(doc_path, words):
    doc = docx.Document(doc_path)
    paragraphs = [paragraph.text for paragraph in doc.paragraphs]

    manager = multiprocessing.Manager()
    found_words = manager.list()
    not_found_words = manager.list(words)

    pool = multiprocessing.Pool(processes=10)
    results = pool.starmap(process_paragraph, [(paragraph_text, words) for paragraph_text in paragraphs])
    print("1111111111-----------")
    for new_text, f_words, nf_words in results:
        print("12222211111-----------")
        found_words.extend(f_words)
        for word in f_words:
            if word in not_found_words:
                print("1111111111")
                not_found_words.remove(word)

    # 重新写入段落文本
    t=0
    for i, paragraph in enumerate(doc.paragraphs):
        t = t + 1
        print(t)
        paragraph.text = results[i][0]

    doc.save('highlighted_document_stone.docx')
    return set(found_words), set(not_found_words)
# 其他函数保持不变
def copy_format(source_run, target_run):
    """将源运行的格式复制到目标运行。"""
    target_run.bold = source_run.bold
    target_run.italic = source_run.italic
    target_run.underline = source_run.underline
    target_run.font.size = source_run.font.size
    target_run.font.name = source_run.font.name
    target_run.font.color.rgb = source_run.font.color.rgb
    target_run.font.highlight_color = source_run.font.highlight_color

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

def save_not_found_words(not_found_words, filename='not_found_words.txt'):
    """将未找到的单词保存到文本文件中。"""
    with open(filename, 'w', encoding='utf-8') as file:
        for word in not_found_words:
            file.write(word + '\n')

def save_found_words(found_words, filename='found_words.txt'):
    """将未找到的单词保存到文本文件中。"""
    with open(filename, 'w', encoding='utf-8') as file:
        for word in found_words:
            file.write(word + '\n')

# 示例使用
if __name__ == '__main__':
    file_path = './CET6_2.json'
    headwords = extract_headwords_from_file(file_path)
    #print(headwords)
    words = headwords
    words = ['trade', 'run', 'jump', 'fly']
    found, not_found = highlight_words_in_doc('Stone.docx', words)

    print("Found words:", found, len(found))
    print("Not found words:", not_found, len(not_found))
    save_not_found_words(not_found)
    save_found_words(found)
