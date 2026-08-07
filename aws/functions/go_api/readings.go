package main

import (
	"strings"
	"unicode"

	"github.com/ikawaha/kagome-dict/ipa"
	"github.com/ikawaha/kagome/v2/tokenizer"
)

var titleTokenizer = mustTitleTokenizer()

// IPADIC intentionally stays small enough for the 512 MB Lambda. Keep rare
// catalog terms that it cannot read here instead of loading the much larger
// UniDic dictionary at runtime.
var titleReadingOverrides = map[string]string{
	"耐量子計算機暗号": "タイリョウシケイサンキアンゴウ",
}

// IPADIC sometimes splits a catalog-specific compound after preserving its
// first kanji as an unknown token. Correct only the known leading compounds;
// the remainder still goes through morphological analysis.
var titleReadingPrefixOverrides = map[string]string{
	"遊環": "ユウカン",
	"詳注": "ショウチュウ",
	"凡事": "ボンジ",
	"湘北": "ショウホク",
}

func mustTitleTokenizer() *tokenizer.Tokenizer {
	t, err := tokenizer.New(ipa.Dict(), tokenizer.OmitBosEos())
	if err != nil {
		panic("initialize Japanese title tokenizer: " + err.Error())
	}
	return t
}

// titleReading converts Japanese title words to their dictionary readings.
// Latin letters and other non-Japanese tokens are preserved so the frontend can
// place them in A-Z groups. Unknown kanji are preserved in the reading value,
// but the frontend deliberately sends them to the catch-all group rather than
// recreating a kanji heading.
func titleReading(title string) string {
	trimmed := strings.TrimSpace(title)
	if reading, ok := titleReadingOverrides[trimmed]; ok {
		return reading
	}
	for prefix, reading := range titleReadingPrefixOverrides {
		if strings.HasPrefix(trimmed, prefix) {
			return reading + titleReading(strings.TrimPrefix(trimmed, prefix))
		}
	}
	var reading strings.Builder
	for _, token := range titleTokenizer.Tokenize(trimmed) {
		if value, ok := token.Reading(); ok && value != "" && value != "*" {
			appendIndexCharacters(&reading, value)
			continue
		}
		appendIndexCharacters(&reading, token.Surface)
	}
	return reading.String()
}

func appendIndexCharacters(reading *strings.Builder, value string) {
	for _, r := range value {
		if digitReading, ok := asciiDigitReadings[r]; ok {
			reading.WriteString(digitReading)
			continue
		}
		if !unicode.IsSpace(r) && !unicode.IsPunct(r) && !unicode.IsSymbol(r) {
			reading.WriteRune(r)
		}
	}
}

var asciiDigitReadings = map[rune]string{
	'0': "ゼロ",
	'1': "イチ",
	'2': "ニ",
	'3': "サン",
	'4': "ヨン",
	'5': "ゴ",
	'6': "ロク",
	'7': "ナナ",
	'8': "ハチ",
	'9': "キュウ",
}
