"""Reader-facing categories. NDC ranges are evidence, not UI labels."""
import re

GENRE_RULES = [
 ('genre-it','IT・プログラミング',['IT','プログラミング','コンピュータ'],[(7,7),(547,548)],r'プログラミング|ソフトウェア|コンピュータ|情報セキュリティ'),
 ('genre-design','デザイン・アート',['デザイン','アート','美術'],[(700,739),(750,759)],r'デザイン|タイポグラフィ|美術|イラスト'),
 ('genre-architecture','建築・まちづくり',['建築','まちづくり','都市計画'],[(518,528)],r'建築|都市計画|まちづくり'),
 ('genre-business','ビジネス・経済',['ビジネス','経営','経済','仕事'],[(330,339),(670,689)],r'経営|起業|マーケティング|簿記'),
 ('genre-science','科学・数学',['科学','数学','自然科学'],[(400,489)],r'数学|物理学|化学|統計学'),
 ('genre-engineering','工学・ものづくり',['工学','ものづくり','電子工作'],[(500,517),(529,589)],r'電子工作|ロボット|機械工学'),
 ('genre-society','社会・歴史',['社会','歴史','政治','法律'],[(200,329),(360,399)],r'歴史|政治|法律|社会学'),
 ('genre-mind','哲学・心理',['哲学','心理学','思想'],[(100,199)],r'哲学|心理学'),
 ('genre-language','言語・語学',['語学','外国語','言語学'],[(800,899)],r'英語|英会話|韓国語|中国語|言語学'),
 ('genre-literature','小説・文学',['小説','文学','詩','エッセイ'],[(900,999)],r'小説|文学|詩集'),
 ('genre-life','暮らし・趣味',['暮らし','趣味','料理','健康'],[(490,499),(590,669),(740,749),(760,799)],r'料理|健康|写真撮影|スポーツ'),
 ('genre-learning','学び・教育',['学習','教育','学び'],[(0,6),(10,99),(370,379)],r'教育|学習法|勉強法'),
]
THEME_GENRES = {
 'cpp':['genre-it'], 'csharp':['genre-it'], 'java':['genre-it'], 'nlp':['genre-it'],
 'security':['genre-it'], 'networks':['genre-it'], 'ux-research':['genre-design','genre-business'],
 'robotics':['genre-engineering'], 'cad':['genre-engineering','genre-architecture'],
 'linear-algebra':['genre-science'], 'calculus':['genre-science'], 'quantum':['genre-science'],
 'japan-history':['genre-society'], 'world-history':['genre-society'],
 'cognitive-psychology':['genre-mind'], 'korean-learning':['genre-language'],
 'c-language':['genre-it'], 'python':['genre-it'], 'javascript':['genre-it'], 'sql':['genre-it'],
 'brutalism':['genre-architecture'], 'english-learning':['genre-language'],
 'typography':['genre-design'], 'ui-design':['genre-design','genre-it'], 'graphic-design':['genre-design'],
 'machine-learning':['genre-it','genre-science'], 'deep-learning':['genre-it','genre-science'],
 'statistics':['genre-science'], 'photography':['genre-life','genre-design'], 'color-theory':['genre-design'],
 'animation':['genre-design'], 'web-design':['genre-design','genre-it'], 'marketing':['genre-business'],
 'accounting':['genre-business'], 'urban-planning':['genre-architecture'],
}


def assign_genres(title, classification, themes):
    match = re.match(r'^(\d{3})(?:\D|$)', str(classification or '').strip())
    ndc = int(match[1]) if match else None
    found = {}
    for ident, label, aliases, ranges, pattern in GENRE_RULES:
        if ndc is not None and any(start <= ndc <= end for start,end in ranges):
            found[ident] = f'分類番号: {classification}'
        if re.search(pattern, title or ''):
            found[ident] = f'書名: {title}'
    for theme in themes:
        for genre in THEME_GENRES.get(theme, []):
            found[genre] = f'確認したテーマ: {theme}'
    return sorted(found.items())


def normalize_search_text(value):
    import unicodedata
    value = unicodedata.normalize('NFKC', value or '').lower()
    value = ''.join(chr(ord(c)-0x60) if 'ァ' <= c <= 'ヶ' else c for c in value)
    # Match Go normalizeQuery while retaining C++ / C# distinctions.
    punctuation = set('・:：,，.．。『』「」"\'“”‘’!?！？-‐‑‒–—―（）()【】[]')
    return ''.join(c for c in value if not c.isspace() and c not in punctuation)
