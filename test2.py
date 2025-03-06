import docx
from nltk import WordNetLemmatizer
from nltk.corpus import wordnet
import nltk
import re
import json

def get_word_forms(word):
    """获取单词的不同形态（基本形式、复数形式、过去式等）。"""
    lemmatizer = WordNetLemmatizer()
    forms = set()
    forms.add(word)
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.VERB))
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.NOUN))
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.ADJ))
    forms.add(lemmatizer.lemmatize(word, pos=wordnet.ADV))
    return forms


def highlight_words_in_doc(doc_path, words):
    """在Word文档中搜索并高亮单词列表中的单词。"""
    # 打开原始文档
    doc = docx.Document(doc_path)

    found_words = set()
    not_found_words = set(words)
    t=0
    print("totle line:",len(doc.paragraphs))
    for paragraph in doc.paragraphs:
        t+=1
        print(t)
        for word in words:
            word_forms = get_word_forms(word)
            for run in paragraph.runs:
                text = run.text
                start_index = 0
                new_text = ""  # 用来保存修改后的文本
                last_pos = 0  # 记录上次匹配的位置

                for form in word_forms:
                    # 使用正则表达式确保匹配完整单词
                    pattern = r'\b' + re.escape(form) + r'\b'
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        start, end = match.span()
                        new_text += text[last_pos:start]  # 添加匹配之前的文本
                        new_text += "<highlight>" + text[start:end] + "</highlight>"  # 高亮文本
                        last_pos = end
                        found_words.add(word)
                        not_found_words.discard(word)

                new_text += text[last_pos:]  # 添加剩余的文本
                run.text = new_text.replace("<highlight>", "")  # 先清空原来的文本

                # 再次处理以保留格式并添加高亮
                if "<highlight>" in new_text:
                    run.text = ""
                    segments = new_text.split("<highlight>")
                    for i, segment in enumerate(segments):
                        if i % 2 == 0:
                            normal_run = paragraph.add_run(segment)
                            copy_format(run, normal_run)
                        else:
                            highlighted_run = paragraph.add_run(segment)
                            copy_format(run, highlighted_run)
                            highlighted_run.font.highlight_color = docx.enum.text.WD_COLOR_INDEX.YELLOW
                else:
                    run.text = new_text

    # 保存修改后的文档
    doc.save('highlighted_document_stone.docx')
    return found_words, not_found_words


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

def save_found_words(not_found_words, filename='found_words.txt'):
    """将未找到的单词保存到文本文件中。"""
    with open(filename, 'w', encoding='utf-8') as file:
        for word in not_found_words:
            file.write(word + '\n')

# 示例使用
file_path = './CET6_2.json'  # 假设这是包含JSON数据的文件
headwords = extract_headwords_from_file(file_path)

# 打印所有提取到的headWord
for i, word in enumerate(headwords, 1):
    print(f"HeadWord {i}: {word}")

# 示例单词列表
words = headwords
# 调用函数
found, not_found = highlight_words_in_doc('Stone.docx', words)

print("Found words:", found,len(found))
print("Not found words:", not_found,len((not_found)))
save_not_found_words(not_found)
save_found_words(found)
