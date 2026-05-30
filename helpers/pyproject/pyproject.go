package main

import (
	"strings"

	"github.com/pelletier/go-toml/v2"
)

// load parses a pyproject.toml into a generic document, preserving every key.
func load(data []byte) (map[string]any, error) {
	doc := map[string]any{}
	if strings.TrimSpace(string(data)) == "" {
		return doc, nil
	}
	if err := toml.Unmarshal(data, &doc); err != nil {
		return nil, err
	}
	return doc, nil
}

// dump re-emits the document as TOML.
func dump(doc map[string]any) ([]byte, error) {
	return toml.Marshal(doc)
}

// table returns the nested table at key, or nil when absent. Safe on a nil map.
func table(doc map[string]any, key string) map[string]any {
	if doc == nil {
		return nil
	}
	t, _ := doc[key].(map[string]any)
	return t
}

// ensureTable returns the nested table at key, creating it when absent.
func ensureTable(doc map[string]any, key string) map[string]any {
	if t, ok := doc[key].(map[string]any); ok {
		return t
	}
	t := map[string]any{}
	doc[key] = t
	return t
}

// getPythonVersion returns the version from a single requires-python specifier,
// stripping a leading comparison operator (e.g. ">=3.14" -> "3.14"). It assumes
// the simple form this SDK writes; a multi-clause range is returned as-is after
// the leading operator.
func getPythonVersion(doc map[string]any) string {
	rp, _ := table(doc, "project")["requires-python"].(string)
	rp = strings.TrimSpace(rp)
	for _, op := range []string{">=", "<=", "==", "!=", "~=", ">", "<"} {
		if strings.HasPrefix(rp, op) {
			return strings.TrimSpace(strings.TrimPrefix(rp, op))
		}
	}
	return rp
}

// getUseUv reports the [tool.dagger].use-uv setting and whether it was set at
// all. Reporting unset (ok == false) lets callers distinguish "not configured"
// from an explicit choice instead of guessing the uv default.
func getUseUv(doc map[string]any) (value bool, ok bool) {
	value, ok = table(table(doc, "tool"), "dagger")["use-uv"].(bool)
	return value, ok
}

func getBaseImage(doc map[string]any) string {
	s, _ := table(table(doc, "tool"), "dagger")["base-image"].(string)
	return s
}

func setPythonVersion(doc map[string]any, v string) {
	ensureTable(doc, "project")["requires-python"] = ">=" + v
}

func setUseUv(doc map[string]any, enabled bool) {
	if enabled {
		// true is the default; keep the file minimal by removing the key.
		removeDaggerKey(doc, "use-uv")
		return
	}
	ensureTable(ensureTable(doc, "tool"), "dagger")["use-uv"] = false
}

func setBaseImage(doc map[string]any, img string) {
	ensureTable(ensureTable(doc, "tool"), "dagger")["base-image"] = img
}

// removeDaggerKey deletes a key from [tool.dagger] and prunes now-empty tables.
func removeDaggerKey(doc map[string]any, key string) {
	tool := table(doc, "tool")
	dagger := table(tool, "dagger")
	if dagger == nil {
		return
	}
	delete(dagger, key)
	if len(dagger) == 0 {
		delete(tool, "dagger")
	}
	if len(tool) == 0 {
		delete(doc, "tool")
	}
}
