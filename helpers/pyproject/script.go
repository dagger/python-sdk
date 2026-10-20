package main

import (
	"fmt"
	"path/filepath"
	"regexp"
	"strings"
)

// The reference regular expression of PEP 723, loosened where the PEP's text,
// which takes precedence over it, allows: a carriage return may end a line, as
// the reference implementation reads the file in text mode, and a block may
// hold no content line, as every field is optional. Go's leftmost-first
// matching prefers what Python's backtracking does, so a block closes at its
// last `# ///` line, as the PEP specifies.
var metadataBlock = regexp.MustCompile(`(?m)^# /// (?P<type>[a-zA-Z0-9-]+)\r?$\s(?P<content>(^#(| .*)\r?$\s)*)^# ///\r?$`)

// scriptBlock is the `script` metadata block of a Python file: its TOML is
// commented out line by line between `# /// script` and `# ///`.
type scriptBlock struct {
	src        []byte
	start, end int // the content lines, comment prefixes included
}

// newline is the line end of the block's opening line, which a line the edit
// adds takes. The TOML itself is edited with LF line ends.
func (b *scriptBlock) newline() string {
	if b.start >= 2 && b.src[b.start-2] == '\r' {
		return "\r\n"
	}
	return "\n"
}

func isScript(path string) bool {
	return filepath.Ext(path) == ".py"
}

// findScriptBlock returns nil for a file with no script block, and refuses
// one with more than one, which PEP 723 says tools must not read.
func findScriptBlock(path string, src []byte) (*scriptBlock, error) {
	var found []*scriptBlock
	typeGroup, contentGroup := metadataBlock.SubexpIndex("type"), metadataBlock.SubexpIndex("content")
	for _, m := range metadataBlock.FindAllSubmatchIndex(src, -1) {
		if string(src[m[2*typeGroup]:m[2*typeGroup+1]]) == "script" {
			found = append(found, &scriptBlock{src: src, start: m[2*contentGroup], end: m[2*contentGroup+1]})
		}
	}
	switch len(found) {
	case 0:
		return nil, nil
	case 1:
		return found[0], nil
	}
	return nil, fmt.Errorf("%s has %d `# /// script` metadata blocks; PEP 723 allows one", filepath.Base(path), len(found))
}

// readScriptBlock refuses a file with no script block too.
func readScriptBlock(path string, src []byte) (*scriptBlock, error) {
	block, err := findScriptBlock(path, src)
	if err == nil && block == nil {
		err = fmt.Errorf("%s has no `# /// script` metadata block", filepath.Base(path))
	}
	return block, err
}

// toml is the block's content with the comment prefixes stripped as PEP 723
// strips them.
func (b *scriptBlock) toml() []byte {
	var out strings.Builder
	for _, line := range b.lines() {
		out.WriteString(lf(uncomment(line)))
	}
	return []byte(out.String())
}

// embed puts edited TOML back into the file. A line the edit left alone keeps
// its bytes, even where stripping its prefix lost them, as `# ` alone does.
func (b *scriptBlock) embed(toml []byte) []byte {
	old := b.lines()
	stripped := make([]string, len(old))
	for i, line := range old {
		stripped[i] = lf(uncomment(line))
	}
	edited := splitLines(string(toml))
	if n := len(edited); n > 0 && !strings.HasSuffix(edited[n-1], "\n") {
		edited[n-1] += "\n"
	}
	kept := commonLines(stripped, edited)
	var out strings.Builder
	out.Write(b.src[:b.start])
	for i, line := range edited {
		if j, ok := kept[i]; ok {
			out.WriteString(old[j])
		} else {
			out.WriteString(strings.TrimSuffix(comment(line), "\n") + b.newline())
		}
	}
	out.Write(b.src[b.end:])
	return []byte(out.String())
}

func (b *scriptBlock) lines() []string {
	return splitLines(string(b.src[b.start:b.end]))
}

func uncomment(line string) string {
	if strings.HasPrefix(line, "# ") {
		return line[2:]
	}
	return line[1:]
}

func lf(line string) string {
	if body, ok := strings.CutSuffix(line, "\r\n"); ok {
		return body + "\n"
	}
	return line
}

func comment(line string) string {
	if line == "\n" {
		return "#\n"
	}
	return "# " + line
}

func splitLines(s string) []string {
	lines := strings.SplitAfter(s, "\n")
	if lines[len(lines)-1] == "" {
		return lines[:len(lines)-1]
	}
	return lines
}

// commonLines maps each line of b that a longest common subsequence keeps
// from a to its index in a.
func commonLines(a, b []string) map[int]int {
	lengths := make([][]int, len(a)+1)
	for i := range lengths {
		lengths[i] = make([]int, len(b)+1)
	}
	for i := len(a) - 1; i >= 0; i-- {
		for j := len(b) - 1; j >= 0; j-- {
			if a[i] == b[j] {
				lengths[i][j] = lengths[i+1][j+1] + 1
			} else {
				lengths[i][j] = max(lengths[i+1][j], lengths[i][j+1])
			}
		}
	}
	kept := map[int]int{}
	for i, j := 0, 0; i < len(a) && j < len(b); {
		switch {
		case a[i] == b[j]:
			kept[j] = i
			i++
			j++
		case lengths[i+1][j] >= lengths[i][j+1]:
			i++
		default:
			j++
		}
	}
	return kept
}

// scriptProjectKeys are the keys a script block holds at its top level and a
// pyproject.toml under [project].
var scriptProjectKeys = []string{"requires-python", "dependencies"}

// asProject reads a script's metadata as a pyproject.toml, so one set of
// readers and writers serves both.
func asProject(doc map[string]any) map[string]any {
	for _, key := range scriptProjectKeys {
		if v, ok := doc[key]; ok {
			ensureTable(doc, "project")[key] = v
			delete(doc, key)
		}
	}
	return doc
}

func fromProject(doc map[string]any) map[string]any {
	project := table(doc, "project")
	for _, key := range scriptProjectKeys {
		if v, ok := project[key]; ok {
			doc[key] = v
		}
	}
	delete(doc, "project")
	return doc
}
