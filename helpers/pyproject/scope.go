package main

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/pelletier/go-toml/v2"
)

// scopeEdit is what the SDK owns in a scope pyproject.toml: the workspace
// members it generates, their sources, and the generated distributions the
// project depends on. GlobalClient nil leaves the flag as it is.
type scopeEdit struct {
	Members      []string
	Sources      []string
	Dependencies []string
	GlobalClient *bool
}

// The SDK owns an entry by its name, not by what is on disk: a stale entry
// whose directory is already gone must still be removed. sdk/, clients/ and
// dagger-clients-* are the SDK's namespace; everything else is the user's.
func ownedMember(path string) bool {
	return path == "sdk" || strings.HasPrefix(path, "clients/")
}

func ownedSource(name string) bool {
	return name == "dagger-io" || strings.HasPrefix(name, "dagger-clients-")
}

func ownedDependency(name string) bool {
	return strings.HasPrefix(name, "dagger-clients-")
}

func normalizeMember(path string) string {
	return strings.TrimSuffix(strings.TrimPrefix(path, "./"), "/")
}

var nameSeparators = regexp.MustCompile(`[-_.]+`)

// normalizeDistribution applies PEP 503 to a distribution name.
func normalizeDistribution(name string) string {
	return nameSeparators.ReplaceAllString(strings.ToLower(name), "-")
}

var requirementName = regexp.MustCompile(`^[A-Za-z0-9._-]+`)

// dependencyName is the distribution a PEP 508 requirement names.
func dependencyName(requirement string) string {
	return normalizeDistribution(requirementName.FindString(strings.TrimSpace(requirement)))
}

// editScope rewrites the SDK-owned entries and nothing else: user tables,
// comments and user entries keep their bytes. The file is parsed before and
// after, so a layout the line editor does not understand is refused rather
// than corrupted.
func editScope(src []byte, edit scopeEdit) ([]byte, error) {
	if _, err := load(src); err != nil {
		return nil, err
	}
	d := &document{text: string(src)}

	d.editArray("tool.uv.workspace", "members", edit.Members, ownedMember, normalizeMember, true)
	d.editSources(edit.Sources)
	if d.section("project") != nil {
		d.editArray("project", "dependencies", edit.Dependencies, ownedDependency, dependencyName, false)
	}
	if edit.GlobalClient != nil {
		d.editGlobalClient(*edit.GlobalClient)
	}

	if err := checkScope([]byte(d.text), edit); err != nil {
		return nil, err
	}
	return []byte(d.text), nil
}

// checkScope reads the result back the way uv will.
func checkScope(out []byte, edit scopeEdit) error {
	doc, err := load(out)
	if err != nil {
		return fmt.Errorf("pyproject.toml has a layout the SDK cannot edit: %w", err)
	}
	fail := func(what string) error {
		return fmt.Errorf("pyproject.toml has a layout the SDK cannot edit: %s", what)
	}
	uv := table(table(doc, "tool"), "uv")
	members, _ := table(uv, "workspace")["members"].([]any)
	if err := checkList(members, edit.Members, ownedMember, normalizeMember); err != nil {
		return fail("[tool.uv.workspace] members " + err.Error())
	}
	sources := table(uv, "sources")
	for _, name := range edit.Sources {
		source, _ := sources[name].(map[string]any)
		if workspace, _ := source["workspace"].(bool); !workspace || len(source) != 1 {
			return fail("[tool.uv.sources] lacks " + name + " = { workspace = true }")
		}
	}
	for name := range sources {
		if ownedSource(normalizeDistribution(name)) && !contains(edit.Sources, name, normalizeDistribution) {
			return fail("[tool.uv.sources] keeps " + name)
		}
	}
	if project := table(doc, "project"); project != nil {
		dependencies, _ := project["dependencies"].([]any)
		if err := checkList(dependencies, edit.Dependencies, ownedDependency, dependencyName); err != nil {
			return fail("[project] dependencies " + err.Error())
		}
	}
	if edit.GlobalClient != nil {
		value, ok := table(table(doc, "tool"), "dagger")["global-client"].(bool)
		if *edit.GlobalClient && !(ok && value) {
			return fail("[tool.dagger] lacks global-client = true")
		}
		if !*edit.GlobalClient && ok {
			return fail("[tool.dagger] keeps global-client")
		}
	}
	return nil
}

func checkList(values []any, want []string, owned func(string) bool, normalize func(string) string) error {
	var have []string
	for _, v := range values {
		if s, ok := v.(string); ok {
			have = append(have, s)
		}
	}
	for _, w := range want {
		if !contains(have, w, normalize) {
			return fmt.Errorf("lacks %q", w)
		}
	}
	for _, h := range have {
		if owned(normalize(h)) && !contains(want, h, normalize) {
			return fmt.Errorf("keeps %q", h)
		}
	}
	return nil
}

func contains(values []string, want string, normalize func(string) string) bool {
	for _, v := range values {
		if normalize(v) == normalize(want) {
			return true
		}
	}
	return false
}

// document is a TOML file edited by byte offsets. Every edit recomputes the
// sections, so offsets never go stale.
type document struct {
	text string
}

// section is one table of the document: its header line and the body up to
// the next header. The root section has no header.
type section struct {
	name        string
	headerStart int
	headerEnd   int // after the header line's newline
	bodyStart   int
	bodyEnd     int
}

func (d *document) sections() []section {
	var found []section
	current := section{name: "", headerStart: -1, headerEnd: 0, bodyStart: 0}
	pos := 0
	for pos < len(d.text) {
		end := strings.IndexByte(d.text[pos:], '\n')
		next := len(d.text)
		if end >= 0 {
			next = pos + end + 1
		}
		if name, ok := headerName(d.text[pos:next]); ok {
			current.bodyEnd = pos
			found = append(found, current)
			current = section{name: name, headerStart: pos, headerEnd: next, bodyStart: next}
		}
		pos = next
	}
	current.bodyEnd = len(d.text)
	return append(found, current)
}

func (d *document) section(name string) *section {
	for _, s := range d.sections() {
		if s.name == name {
			s := s
			return &s
		}
	}
	return nil
}

// headerName reads `[a.b]` or `[[a.b]]`, with any spacing and quoting of the
// parts and a trailing comment, into "a.b".
func headerName(line string) (string, bool) {
	trimmed := strings.TrimSpace(line)
	if !strings.HasPrefix(trimmed, "[") {
		return "", false
	}
	inner := strings.TrimPrefix(strings.TrimPrefix(trimmed, "["), "[")
	close := strings.IndexByte(inner, ']')
	if close < 0 {
		return "", false
	}
	var parts []string
	for _, part := range splitOutsideQuotes(inner[:close], '.') {
		parts = append(parts, strings.Trim(strings.TrimSpace(part), `"'`))
	}
	return strings.Join(parts, "."), true
}

func splitOutsideQuotes(s string, sep byte) []string {
	var parts []string
	start := 0
	for i := 0; i < len(s); {
		switch s[i] {
		case '"', '\'':
			i = skipString(s, i)
		case sep:
			parts = append(parts, s[start:i])
			start = i + 1
			i++
		default:
			i++
		}
	}
	return append(parts, s[start:])
}

// skipString returns the offset after the string that starts at i.
func skipString(s string, i int) int {
	q := s[i]
	if strings.HasPrefix(s[i:], strings.Repeat(string(q), 3)) {
		end := strings.Index(s[i+3:], strings.Repeat(string(q), 3))
		if end < 0 {
			return len(s)
		}
		return i + 3 + end + 3
	}
	j := i + 1
	for j < len(s) && s[j] != q {
		if q == '"' && s[j] == '\\' {
			j++
		}
		j++
	}
	if j < len(s) {
		j++
	}
	return j
}

// valueEnd is the offset after the value that starts at start: a bracketed
// value runs to its matching bracket across lines, any other to the end of
// its line before a comment.
func valueEnd(s string, start int) int {
	if start >= len(s) {
		return start
	}
	if s[start] == '[' || s[start] == '{' {
		depth := 0
		for i := start; i < len(s); {
			switch s[i] {
			case '"', '\'':
				i = skipString(s, i)
				continue
			case '#':
				for i < len(s) && s[i] != '\n' {
					i++
				}
				continue
			case '[', '{':
				depth++
			case ']', '}':
				depth--
				if depth == 0 {
					return i + 1
				}
			}
			i++
		}
		return len(s)
	}
	i := start
	for i < len(s) && s[i] != '\n' && s[i] != '#' {
		if s[i] == '"' || s[i] == '\'' {
			i = skipString(s, i)
			continue
		}
		i++
	}
	for i > start && (s[i-1] == ' ' || s[i-1] == '\t') {
		i--
	}
	return i
}

// keyValue locates `key = value` in a section: the line's start, the value's
// span, and the line's leading whitespace.
type keyValue struct {
	lineStart  int
	valueStart int
	valueEnd   int
	indent     string
}

func (d *document) findKey(sec *section, key string) *keyValue {
	quoted := regexp.QuoteMeta(key)
	pattern := regexp.MustCompile(`^([ \t]*)(?:"` + quoted + `"|'` + quoted + `'|` + quoted + `)[ \t]*=[ \t]*`)
	for _, line := range d.lines(sec) {
		m := pattern.FindStringSubmatchIndex(d.text[line.start:line.end])
		if m == nil {
			continue
		}
		valueStart := line.start + m[1]
		return &keyValue{
			lineStart:  line.start,
			valueStart: valueStart,
			valueEnd:   valueEnd(d.text, valueStart),
			indent:     d.text[line.start+m[2] : line.start+m[3]],
		}
	}
	return nil
}

type line struct {
	start, end int // end excludes the newline
}

// lines of a section body that start a statement: the lines inside a
// multi-line value are skipped, so an array element never passes for a key.
func (d *document) lines(sec *section) []line {
	var found []line
	pos := sec.bodyStart
	for pos < sec.bodyEnd {
		l := line{start: pos, end: sec.bodyEnd}
		next := sec.bodyEnd
		if nl := strings.IndexByte(d.text[pos:sec.bodyEnd], '\n'); nl >= 0 {
			l.end = pos + nl
			next = l.end + 1
		}
		found = append(found, l)
		pos = next
		text := d.text[l.start:l.end]
		eq := strings.IndexByte(stripComments(text), '=')
		if isComment(text) || eq < 0 {
			continue
		}
		valueStart := l.start + eq + 1
		for valueStart < l.end && (d.text[valueStart] == ' ' || d.text[valueStart] == '\t') {
			valueStart++
		}
		if after := valueEnd(d.text, valueStart); after > pos {
			pos = len(d.text)
			if nl := strings.IndexByte(d.text[after:], '\n'); nl >= 0 {
				pos = after + nl + 1
			}
		}
	}
	return found
}

func isBlank(s string) bool {
	return strings.TrimSpace(s) == ""
}

func isComment(s string) bool {
	return strings.HasPrefix(strings.TrimSpace(s), "#")
}

// insertionPoint is where a new key line goes in a section: after its last
// non-blank line, or right after the header of an empty one.
func (d *document) insertionPoint(sec *section) (int, string) {
	at := sec.headerEnd
	indent := ""
	if sec.headerStart >= 0 {
		header := d.text[sec.headerStart:sec.headerEnd]
		indent = header[:len(header)-len(strings.TrimLeft(header, " \t"))]
	}
	for _, l := range d.lines(sec) {
		text := d.text[l.start:l.end]
		if isBlank(text) {
			continue
		}
		at = l.end
		if at < len(d.text) && d.text[at] == '\n' {
			at++
		}
		if !isComment(text) {
			indent = text[:len(text)-len(strings.TrimLeft(text, " \t"))]
		}
	}
	return at, indent
}

func (d *document) insertLine(sec *section, text string) {
	at, indent := d.insertionPoint(sec)
	// The line before the point may lack its newline, at the end of the file.
	if at > 0 && d.text[at-1] != '\n' {
		d.text = d.text[:at] + "\n" + d.text[at:]
		at++
	}
	d.text = d.text[:at] + indent + text + "\n" + d.text[at:]
}

// ensureSection appends a table at the end when the document has none.
func (d *document) ensureSection(name string) *section {
	if sec := d.section(name); sec != nil {
		return sec
	}
	if d.text != "" && !strings.HasSuffix(d.text, "\n") {
		d.text += "\n"
	}
	if d.text != "" {
		d.text += "\n"
	}
	d.text += "[" + name + "]\n"
	return d.section(name)
}

func (d *document) deleteLine(lineStart int) {
	end := strings.IndexByte(d.text[lineStart:], '\n')
	if end < 0 {
		d.text = d.text[:lineStart]
		return
	}
	d.text = d.text[:lineStart] + d.text[lineStart+end+1:]
}

func (d *document) replace(start, end int, with string) {
	d.text = d.text[:start] + with + d.text[end:]
}

func quote(s string) string {
	return strconv.Quote(s)
}

// editArray sets the owned entries of a string array, in the array's own
// style. A missing key is added to the section, which must exist unless
// create says otherwise.
func (d *document) editArray(sectionName, key string, want []string, owned func(string) bool, normalize func(string) string, create bool) {
	sec := d.section(sectionName)
	if sec == nil {
		if !create {
			return
		}
		sec = d.ensureSection(sectionName)
	}
	kv := d.findKey(sec, key)
	if kv == nil {
		var quoted []string
		for _, w := range want {
			quoted = append(quoted, quote(w))
		}
		d.insertLine(sec, key+" = ["+strings.Join(quoted, ", ")+"]")
		return
	}
	raw := d.text[kv.valueStart:kv.valueEnd]
	if !strings.HasPrefix(raw, "[") {
		return
	}
	if edited, changed := editArrayText(raw, want, owned, normalize); changed {
		d.replace(kv.valueStart, kv.valueEnd, edited)
	}
}

// arrayElement is one element of an array with the bytes around it, so an
// element the SDK does not own goes back exactly as it came.
type arrayElement struct {
	raw   string
	value string
	isStr bool
}

func editArrayText(raw string, want []string, owned func(string) bool, normalize func(string) string) (string, bool) {
	inner := raw[1 : len(raw)-1]
	elements, tail := splitArray(inner)
	multiline := strings.Contains(inner, "\n")

	var kept []arrayElement
	changed := false
	for _, e := range elements {
		if e.isStr && owned(normalize(e.value)) && !contains(want, e.value, normalize) {
			changed = true
			continue
		}
		kept = append(kept, e)
	}
	var added []string
	for _, w := range want {
		present := false
		for _, e := range kept {
			if e.isStr && normalize(e.value) == normalize(w) {
				present = true
				break
			}
		}
		if !present {
			added = append(added, w)
		}
	}
	if len(added) == 0 && !changed {
		return raw, false
	}

	var parts []string
	for _, e := range kept {
		parts = append(parts, e.raw)
	}
	if multiline {
		indent := elementIndent(elements)
		// The tail is what sits between the last element and the bracket: a
		// comment on the element's line, then the bracket's own indentation.
		head, closing := "", tail
		if nl := strings.LastIndexByte(tail, '\n'); nl >= 0 {
			head, closing = tail[:nl], tail[nl+1:]
		}
		text := "["
		if len(parts) > 0 {
			text += strings.Join(parts, ",") + ","
		}
		if len(added) == 0 {
			return text + tail + "]", true
		}
		var fresh []string
		for _, a := range added {
			fresh = append(fresh, "\n"+indent+quote(a))
		}
		return text + head + strings.Join(fresh, ",") + ",\n" + closing + "]", true
	}
	for _, a := range added {
		parts = append(parts, " "+quote(a))
	}
	if len(parts) > 0 {
		parts[0] = strings.TrimLeft(parts[0], " \t")
	}
	return "[" + strings.Join(parts, ",") + "]", true
}

// elementIndent is the indentation of the elements, for a new one to match.
func elementIndent(elements []arrayElement) string {
	for i := len(elements) - 1; i >= 0; i-- {
		raw := elements[i].raw
		if nl := strings.LastIndexByte(raw, '\n'); nl >= 0 {
			rest := raw[nl+1:]
			return rest[:len(rest)-len(strings.TrimLeft(rest, " \t"))]
		}
	}
	return "    "
}

// splitArray cuts the inside of an array at its top-level commas. What
// follows the last comma is the tail unless it holds a value.
func splitArray(inner string) ([]arrayElement, string) {
	var segments []string
	start := 0
	depth := 0
	for i := 0; i < len(inner); {
		switch inner[i] {
		case '"', '\'':
			i = skipString(inner, i)
			continue
		case '#':
			for i < len(inner) && inner[i] != '\n' {
				i++
			}
			continue
		case '[', '{':
			depth++
		case ']', '}':
			depth--
		case ',':
			if depth == 0 {
				segments = append(segments, inner[start:i])
				start = i + 1
			}
		}
		i++
	}
	last := inner[start:]
	tail := ""
	if isBlank(stripComments(last)) {
		tail = last
	} else {
		segments = append(segments, last)
	}
	var elements []arrayElement
	for _, seg := range segments {
		value, ok := stringValue(seg)
		elements = append(elements, arrayElement{raw: seg, value: value, isStr: ok})
	}
	return elements, tail
}

func stripComments(s string) string {
	var out strings.Builder
	for i := 0; i < len(s); {
		switch s[i] {
		case '"', '\'':
			end := skipString(s, i)
			out.WriteString(s[i:end])
			i = end
		case '#':
			for i < len(s) && s[i] != '\n' {
				i++
			}
		default:
			out.WriteByte(s[i])
			i++
		}
	}
	return out.String()
}

// stringValue reads a segment that holds one string literal.
func stringValue(seg string) (string, bool) {
	s := strings.TrimSpace(stripComments(seg))
	if len(s) < 2 {
		return "", false
	}
	switch {
	case s[0] == '"' && s[len(s)-1] == '"' && skipString(s, 0) == len(s):
		value, err := strconv.Unquote(s)
		if err != nil {
			return "", false
		}
		return value, true
	case s[0] == '\'' && s[len(s)-1] == '\'' && skipString(s, 0) == len(s):
		return s[1 : len(s)-1], true
	}
	return "", false
}

// editSources points every owned source at the workspace, drops the owned
// ones that are gone, and keeps the user's.
func (d *document) editSources(want []string) {
	sec := d.ensureSection("tool.uv.sources")
	var stale []int
	var keep []string
	for _, l := range d.lines(sec) {
		key, ok := lineKey(d.text[l.start:l.end])
		if !ok || !ownedSource(normalizeDistribution(key)) {
			continue
		}
		if contains(want, key, normalizeDistribution) {
			keep = append(keep, key)
		} else {
			stale = append(stale, l.start)
		}
	}
	// Last first, so the offsets before each deletion hold.
	for i := len(stale) - 1; i >= 0; i-- {
		d.deleteLine(stale[i])
	}
	for _, key := range keep {
		kv := d.findKey(d.section("tool.uv.sources"), key)
		if !isWorkspaceSource(d.text[kv.valueStart:kv.valueEnd]) {
			d.replace(kv.valueStart, kv.valueEnd, "{ workspace = true }")
		}
	}
	for _, w := range want {
		if !contains(keep, w, normalizeDistribution) {
			d.insertLine(d.section("tool.uv.sources"), w+" = { workspace = true }")
		}
	}
}

var keyLine = regexp.MustCompile(`^[ \t]*("[^"]*"|'[^']*'|[A-Za-z0-9_-]+)[ \t]*=`)

func lineKey(text string) (string, bool) {
	m := keyLine.FindStringSubmatch(text)
	if m == nil {
		return "", false
	}
	return strings.Trim(m[1], `"'`), true
}

func isWorkspaceSource(value string) bool {
	var parsed map[string]any
	if err := toml.Unmarshal([]byte("x = "+value), &parsed); err != nil {
		return false
	}
	source, _ := parsed["x"].(map[string]any)
	workspace, _ := source["workspace"].(bool)
	return workspace && len(source) == 1
}

// editGlobalClient writes or clears [tool.dagger] global-client, pruning a
// table the flag alone kept.
func (d *document) editGlobalClient(on bool) {
	sec := d.section("tool.dagger")
	if on {
		if sec == nil {
			sec = d.ensureSection("tool.dagger")
		}
		if kv := d.findKey(sec, "global-client"); kv != nil {
			if strings.TrimSpace(d.text[kv.valueStart:kv.valueEnd]) != "true" {
				d.replace(kv.valueStart, kv.valueEnd, "true")
			}
			return
		}
		d.insertLine(sec, "global-client = true")
		return
	}
	if sec == nil {
		return
	}
	kv := d.findKey(sec, "global-client")
	if kv == nil {
		return
	}
	d.deleteLine(kv.lineStart)
	sec = d.section("tool.dagger")
	for _, l := range d.lines(sec) {
		if text := d.text[l.start:l.end]; !isBlank(text) && !isComment(text) {
			return
		}
	}
	d.text = d.text[:sec.headerStart] + d.text[sec.bodyEnd:]
	d.text = strings.TrimRight(d.text, "\n") + "\n"
	if strings.HasSuffix(d.text, "\n\n") {
		d.text = strings.TrimSuffix(d.text, "\n")
	}
}
