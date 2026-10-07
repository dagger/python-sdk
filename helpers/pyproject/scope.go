package main

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/pelletier/go-toml/v2"
)

// scopeEdit is what the SDK owns in a scope pyproject.toml: the workspace
// members it generates, their sources, and the generated distributions the
// project depends on. Stale names the members generation made before and
// removes now; GlobalClient nil leaves the flag as it is.
type scopeEdit struct {
	Members      []string
	Stale        []string
	Sources      []string
	Dependencies []string
	GlobalClient *bool
}

// ownedMember reports whether generation may remove a workspace member. The
// caller names them: a member under clients/ may be the user's, and only the
// marker in its directory says otherwise, which the editor cannot see.
// Sources and dependencies are owned by name, dagger-io and dagger-clients-*
// being the SDK's namespace.
func (e scopeEdit) ownedMember(path string) bool {
	return contains(e.Stale, path, normalizeMember)
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

	d.editArray("tool.uv.workspace", "members", edit.Members, edit.ownedMember, normalizeMember, true)
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

// The forms the editor asks for when it refuses a file. They are the
// editor's own limits: its line editor finds the workspace and sources
// tables by their headers, not as inline tables or dotted keys. The module
// runtime reads those tables with tomllib and has no such limit.
const (
	workspaceForm = "Write the workspace table as an unquoted [tool.uv.workspace] header with members as an array of strings"
	sourcesForm   = "the sources as an unquoted [tool.uv.sources] header with one key per source, such as dagger-io = { workspace = true }"
	regenerate    = ", then run dagger generate again."
)

// checkScope reads the result back the way uv will.
func checkScope(out []byte, edit scopeEdit) error {
	doc, err := load(out)
	if err != nil {
		// The edit broke a file that parsed before, so a table was written in
		// a shape the line editor does not see; which one, the parser cannot say.
		return fmt.Errorf("pyproject.toml has a layout the SDK cannot edit: %w. %s, and %s%s", err, workspaceForm, sourcesForm, regenerate)
	}
	fail := func(what string) error {
		return fmt.Errorf("pyproject.toml has a layout the SDK cannot edit: %s", what)
	}
	uv := table(table(doc, "tool"), "uv")
	members, _ := table(uv, "workspace")["members"].([]any)
	if err := checkList(members, edit.Members, edit.ownedMember, normalizeMember); err != nil {
		return fail("[tool.uv.workspace] members " + err.Error() + ". " + workspaceForm + regenerate)
	}
	sources := table(uv, "sources")
	for _, name := range edit.Sources {
		source, _ := sources[name].(map[string]any)
		if workspace, _ := source["workspace"].(bool); !workspace || len(source) != 1 {
			return fail("[tool.uv.sources] lacks " + name + " = { workspace = true }. Write " + sourcesForm + regenerate)
		}
	}
	for name := range sources {
		if ownedSource(normalizeDistribution(name)) && !contains(edit.Sources, name, normalizeDistribution) {
			return fail("[tool.uv.sources] keeps " + name + ". Write " + sourcesForm + regenerate)
		}
	}
	if project := table(doc, "project"); project != nil {
		dependencies, _ := project["dependencies"].([]any)
		if err := checkList(dependencies, edit.Dependencies, ownedDependency, dependencyName); err != nil {
			return fail("[project] dependencies " + err.Error())
		}
	}
	if edit.GlobalClient != nil {
		return checkGlobalClient(doc, *edit.GlobalClient)
	}
	return nil
}

func checkGlobalClient(doc map[string]any, on bool) error {
	value, ok := table(table(doc, "tool"), "dagger")["global-client"].(bool)
	if on && !(ok && value) {
		return fmt.Errorf("pyproject.toml has a layout the SDK cannot edit: [tool.dagger] lacks global-client = true")
	}
	if !on && ok {
		return fmt.Errorf("pyproject.toml has a layout the SDK cannot edit: [tool.dagger] keeps global-client")
	}
	return nil
}

// editGlobalClient writes or clears the global client flag alone, with the
// same care as editScope: `mod config set` owns that one key, not the file.
func editGlobalClient(src []byte, on bool) ([]byte, error) {
	if _, err := load(src); err != nil {
		return nil, err
	}
	d := &document{text: string(src)}
	d.editGlobalClient(on)
	doc, err := load([]byte(d.text))
	if err != nil {
		return nil, fmt.Errorf("pyproject.toml has a layout the SDK cannot edit: %w", err)
	}
	if err := checkGlobalClient(doc, on); err != nil {
		return nil, err
	}
	return []byte(d.text), nil
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
			pos = next
			continue
		}
		pos = d.statementEnd(pos, strings.TrimSuffix(d.text[pos:next], "\n"))
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
		pos = max(next, d.statementEnd(l.start, d.text[l.start:l.end]))
	}
	return found
}

// statementEnd is the offset of the line after the statement on the line at
// start, past the lines of a multi-line value: a `[` that opens one of those
// lines is inside a string or an array, not a table header.
func (d *document) statementEnd(start int, text string) int {
	next := start + len(text)
	if next < len(d.text) {
		next++
	}
	eq := strings.IndexByte(stripComments(text), '=')
	if isComment(text) || eq < 0 {
		return next
	}
	valueStart := start + eq + 1
	for valueStart < start+len(text) && (d.text[valueStart] == ' ' || d.text[valueStart] == '\t') {
		valueStart++
	}
	after := valueEnd(d.text, valueStart)
	if after <= next {
		return next
	}
	if nl := strings.IndexByte(d.text[after:], '\n'); nl >= 0 {
		return after + nl + 1
	}
	return len(d.text)
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

// removeTable cuts one table, its header and body up to the next header, out
// of a file the user wrote, and keeps every other byte. The result is parsed
// back, so a layout the line model misreads fails here instead of reaching
// the caller as a different file.
func removeTable(src []byte, name string) ([]byte, error) {
	d := &document{text: string(src)}
	sec := d.section(name)
	if sec == nil || sec.headerStart < 0 {
		return src, nil
	}
	d.replace(sec.headerStart, sec.bodyEnd, "")
	if _, err := load([]byte(d.text)); err != nil {
		return nil, fmt.Errorf("removing [%s] leaves a file that is not TOML: %w", name, err)
	}
	return []byte(d.text), nil
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

	keep := make([]bool, len(elements))
	changed := false
	for i, e := range elements {
		keep[i] = !(e.isStr && owned(normalize(e.value)) && !contains(want, e.value, normalize))
		changed = changed || !keep[i]
	}
	var added []string
	for _, w := range want {
		present := false
		for i, e := range elements {
			if keep[i] && e.isStr && normalize(e.value) == normalize(w) {
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

	if multiline {
		return editMultilineArray(elements, keep, tail, added), true
	}
	var parts []string
	for i, e := range elements {
		if keep[i] {
			parts = append(parts, e.raw)
		}
	}
	for _, a := range added {
		parts = append(parts, " "+quote(a))
	}
	if len(parts) > 0 {
		parts[0] = strings.TrimLeft(parts[0], " \t")
	}
	return "[" + strings.Join(parts, ",") + "]", true
}

// editMultilineArray writes an array one element a line, in its own style.
// A comment after an element's comma is cut into the next segment, or into
// the tail, but it belongs to the element's line: it goes with the element
// when that is removed, and stays when the element does.
func editMultilineArray(elements []arrayElement, keep []bool, tail string, added []string) string {
	indent := elementIndent(elements)
	if tail == "" && len(elements) > 0 {
		// No trailing comma: what follows the last value is the tail.
		last := &elements[len(elements)-1]
		value := strings.TrimRight(last.raw, " \t\r\n")
		last.raw, tail = value, last.raw[len(value):]
	}
	leads := make([]string, len(elements))
	bodies := make([]string, len(elements))
	for i, e := range elements {
		leads[i], bodies[i] = lineComment(e.raw)
	}
	tailLead, rest := lineComment(tail)
	// The comment of the line the bracket opens on is the bracket's.
	text := "["
	if len(elements) > 0 {
		text += leads[0]
	}
	for i := range elements {
		if !keep[i] {
			continue
		}
		own := tailLead
		if i+1 < len(elements) {
			own = leads[i+1]
		}
		text += bodies[i] + "," + own
	}
	if len(added) == 0 {
		return text + rest + "]"
	}
	// New elements go after any comment lines, before the bracket's line.
	head, closing := "", rest
	if nl := strings.LastIndexByte(rest, '\n'); nl >= 0 {
		head, closing = rest[:nl], rest[nl+1:]
	}
	for _, a := range added {
		text += head + "\n" + indent + quote(a) + ","
		head = ""
	}
	return text + "\n" + closing + "]"
}

// lineComment splits off what a segment holds before its first newline when
// that is only blanks and a comment: the rest of the line before it.
func lineComment(segment string) (string, string) {
	nl := strings.IndexByte(segment, '\n')
	if nl < 0 || !isBlank(stripComments(segment[:nl])) {
		return "", segment
	}
	return segment[:nl], segment[nl:]
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
// ones that are gone, and keeps the user's. A source is a key of
// [tool.uv.sources] or a table of its own, [tool.uv.sources.<name>], which is
// how a file re-marshaled by a TOML library writes it; a new source follows
// the file's style.
func (d *document) editSources(want []string) {
	tables := d.editSourceTables(want)

	var stale []int
	var keep, present []string
	if sec := d.section(sourcesTable); sec != nil {
		// A source may also be written as dotted keys, one line a field.
		dotted := map[string][]line{}
		var dottedNames []string
		for _, l := range d.lines(sec) {
			key, field, ok := lineKey(d.text[l.start:l.end])
			if !ok || !ownedSource(normalizeDistribution(key)) {
				continue
			}
			switch {
			case !contains(want, key, normalizeDistribution):
				stale = append(stale, l.start)
			case field:
				name := normalizeDistribution(key)
				if _, seen := dotted[name]; !seen {
					dottedNames = append(dottedNames, key)
				}
				dotted[name] = append(dotted[name], l)
			default:
				keep = append(keep, key)
			}
		}
		for _, key := range dottedNames {
			lines := dotted[normalizeDistribution(key)]
			if len(lines) == 1 && isWorkspaceLine(d.text[lines[0].start:lines[0].end]) {
				present = append(present, key)
				continue
			}
			// Any other field: the source is written again, inline.
			for _, l := range lines {
				stale = append(stale, l.start)
			}
		}
	}
	sort.Ints(stale)
	// Last first, so the offsets before each deletion hold.
	for i := len(stale) - 1; i >= 0; i-- {
		d.deleteLine(stale[i])
	}
	for _, key := range keep {
		kv := d.findKey(d.section(sourcesTable), key)
		if !isWorkspaceSource(d.text[kv.valueStart:kv.valueEnd]) {
			d.replace(kv.valueStart, kv.valueEnd, "{ workspace = true }")
		}
	}
	present = append(present, keep...)
	for _, w := range want {
		if contains(present, w, normalizeDistribution) || contains(tables, w, normalizeDistribution) {
			continue
		}
		if d.section(sourcesTable) == nil && len(tables) > 0 {
			d.appendSourceTable(w)
			continue
		}
		d.insertLine(d.ensureSection(sourcesTable), w+" = { workspace = true }")
	}
}

const sourcesTable = "tool.uv.sources"

// sourceTableName is the source a [tool.uv.sources.<name>] table is for.
func sourceTableName(sec section) (string, bool) {
	return strings.CutPrefix(sec.name, sourcesTable+".")
}

// editSourceTables applies editSources to the sources written as tables, one
// edit at a time because each moves the offsets after it, and returns the
// names of every source table left.
func (d *document) editSourceTables(want []string) []string {
	for d.editOneSourceTable(want) {
	}
	var names []string
	for _, sec := range d.sections() {
		if name, ok := sourceTableName(sec); ok {
			names = append(names, name)
		}
	}
	return names
}

func (d *document) editOneSourceTable(want []string) bool {
	for _, sec := range d.sections() {
		name, ok := sourceTableName(sec)
		if !ok || !ownedSource(normalizeDistribution(name)) {
			continue
		}
		if !contains(want, name, normalizeDistribution) {
			d.text = d.text[:sec.headerStart] + d.text[sec.bodyEnd:]
			return true
		}
		body := d.text[sec.bodyStart:sec.bodyEnd]
		if !isWorkspaceTable(body) {
			// The blank lines that part it from the next table stay.
			end := sec.bodyStart + len(strings.TrimRight(body, " \t\r\n"))
			d.replace(sec.bodyStart, end, "workspace = true")
			return true
		}
	}
	return false
}

// appendSourceTable adds a source table after the last one, before the blank
// lines that part it from what follows.
func (d *document) appendSourceTable(name string) {
	var last section
	for _, sec := range d.sections() {
		if _, ok := sourceTableName(sec); ok {
			last = sec
		}
	}
	body := d.text[last.bodyStart:last.bodyEnd]
	at := last.bodyStart + len(strings.TrimRight(body, " \t\r\n"))
	if at < len(d.text) && d.text[at] == '\n' {
		at++
	} else {
		d.text = d.text[:at] + "\n" + d.text[at:]
		at++
	}
	d.text = d.text[:at] + "\n[" + sourcesTable + "." + name + "]\nworkspace = true\n" + d.text[at:]
}

func isWorkspaceTable(body string) bool {
	var source map[string]any
	if err := toml.Unmarshal([]byte(body), &source); err != nil {
		return false
	}
	workspace, _ := source["workspace"].(bool)
	return workspace && len(source) == 1
}

const keyPart = `(?:"[^"]*"|'[^']*'|[A-Za-z0-9_-]+)`

var keyLine = regexp.MustCompile(`^[ \t]*(` + keyPart + `)((?:[ \t]*\.[ \t]*` + keyPart + `)*)[ \t]*=`)

// lineKey reads the key a line sets, and whether it sets a field of it with
// a dotted key, as in `dagger-io.workspace = true`.
func lineKey(text string) (string, bool, bool) {
	m := keyLine.FindStringSubmatch(text)
	if m == nil {
		return "", false, false
	}
	return strings.Trim(m[1], `"'`), m[2] != "", true
}

// isWorkspaceLine reports whether one line alone makes a workspace source.
func isWorkspaceLine(text string) bool {
	var parsed map[string]any
	if err := toml.Unmarshal([]byte(text), &parsed); err != nil || len(parsed) != 1 {
		return false
	}
	for _, v := range parsed {
		source, _ := v.(map[string]any)
		workspace, _ := source["workspace"].(bool)
		return workspace && len(source) == 1
	}
	return false
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
	// A comment left in the table is the user's, and keeps the table.
	if !isBlank(d.text[sec.bodyStart:sec.bodyEnd]) {
		return
	}
	before := d.text[:sec.headerStart]
	// ensureSection opened a last table with one blank line; take it back.
	if sec.bodyEnd == len(d.text) && strings.HasSuffix(before, "\n\n") {
		before = strings.TrimSuffix(before, "\n")
	}
	d.text = before + d.text[sec.bodyEnd:]
}
