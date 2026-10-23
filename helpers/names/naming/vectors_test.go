package naming

import (
	"encoding/json"
	"os"
	"testing"
)

// TestVectors checks the copy against the engine's shared test vectors, which
// were copied with it: every input formats, in every casing, as the engine
// formats it.
func TestVectors(t *testing.T) {
	data, err := os.ReadFile("testdata/vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Input   string            `json:"input"`
		Formats map[string]string `json:"formats"`
	}
	if err := json.Unmarshal(data, &vectors); err != nil {
		t.Fatal(err)
	}
	if len(vectors) == 0 {
		t.Fatal("no vectors")
	}
	formats := map[string]struct {
		casing Casing
		style  AcronymStyle
	}{
		"PASCAL":             {Pascal, Uppercase},
		"PASCAL_CAPITALIZED": {Pascal, Capitalized},
		"CAMEL":              {Camel, Uppercase},
		"CAMEL_CAPITALIZED":  {Camel, Capitalized},
		"SNAKE":              {Snake, Uppercase},
		"SCREAMING_SNAKE":    {ScreamingSnake, Uppercase},
		"KEBAB":              {Kebab, Uppercase},
		"FLAT":               {Flat, Uppercase},
	}
	for _, v := range vectors {
		id, err := DictionaryFor(FirstVersion).Parse(v.Input)
		if err != nil {
			t.Errorf("%q: %v", v.Input, err)
			continue
		}
		for name, want := range v.Formats {
			f, ok := formats[name]
			if !ok {
				t.Errorf("%q: unknown format %s", v.Input, name)
				continue
			}
			if got := id.Format(f.casing, f.style); got != want {
				t.Errorf("%q in %s: got %q, want %q", v.Input, name, got, want)
			}
		}
	}
}
