"""Conservative, evidence-bearing rules; no guessed reading difficulty."""
import re
import unicodedata


def norm(value):
    return unicodedata.normalize('NFKC', value or '').lower()


def classify_level(description):
    patterns = {
        'beginner': r'(?:初心者|初学者|入門者)(?:向け|を対象|のための|にもわかる|にも分かる)',
        'intermediate': r'中級者(?:向け|を対象|のための)',
        'advanced': r'(?<!中)上級者(?:向け|を対象|のための)',
    }
    if re.search(r'中上級|初中級|初心者から|初級から|中級(?:者)?から', description or ''): return None, None
    matches = {}
    for sentence in re.split(r'[。.!?！？\n]', description or ''):
        # Refuse ranges and negated/contrasting audience claims.
        if re.search(r'初心者から|初級から|中級(?:者)?から|ではな|ではあり|向けでない|向けではない|限らず|だけでなく|のみならず', sentence):
            continue
        if re.search(r'後者|前者|物足り|欠如|絶対|説明しない|初心者向けヒント|初心者向けチュートリアル|初学者向けでも|第[0-9一二三四五六七八九]|[0-9]+章', sentence):
            continue
        if not re.search(r'本書|本企画|対象読者|入門書|テキスト|ガイド|書籍|教科書|解説書|教材|定番|名著|初心者向けに|初学者向けに|初学者を対象|入門者を対象', sentence):
            continue
        for level, pattern in patterns.items():
            if re.search(pattern, sentence): matches[level] = sentence.strip()
    if len(matches) != 1: return None, None
    level, quote = next(iter(matches.items()))
    return level, {'field':'description', 'quote':quote, 'method':'explicit_audience_rule_v1'}


def theme_evidence(title, categories, description, pattern):
    if re.search(pattern, norm(title)):
        return f'書名: {title}'
    for category in categories:
        if re.search(pattern, norm(category)):
            return f'書誌カテゴリ: {category}'
    # A passing reference in a blurb is insufficient to classify the whole book.
    if len(re.findall(pattern, norm(description))) >= 2:
        sentence = next((s.strip() for s in re.split(r'[。\n]', description) if re.search(pattern, norm(s))), '')
        return f'内容紹介（同じテーマの複数回言及）: {sentence}'
    return None
