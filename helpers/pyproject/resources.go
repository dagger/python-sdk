package main

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
)

type resource struct {
	Pattern string `json:"pattern"`
	Rooted  string `json:"rooted"`
}

// deriveResources follows the module's path sources the way uv does, through
// its workspace members and each path source's own, so the module loads what
// its build reads. tree holds only the workspace's pyproject.toml files at
// their workspace paths and the module's uv.lock, so errors name files by
// those paths.
func deriveResources(tree, module string) ([]resource, error) {
	module = path.Clean(module)
	resources := []resource{}
	doc, err := readProject(tree, module)
	if err != nil {
		return nil, fmt.Errorf("%s %w", path.Join(module, "pyproject.toml"), err)
	}
	if doc == nil {
		return resources, nil
	}
	type project struct {
		dir string
		doc map[string]any
	}
	added := map[string]bool{}
	add := func(rooted, from string) error {
		if _, inside := within(module, rooted); inside || added[rooted] {
			return nil
		}
		// The engine reads every backslash in a workspace path as "/", so it
		// would load another path than this one.
		if strings.Contains(rooted, `\`) {
			return fmt.Errorf("%s holds a backslash, which Dagger reads as a path separator; rename the directory to load it", from)
		}
		added[rooted] = true
		resources = append(resources, resource{Pattern: modulePath(module, rooted), Rooted: rooted})
		return nil
	}
	seen := map[string]bool{module: true}
	queue := []project{{module, doc}}
	visit := func(dir string) error {
		if seen[dir] {
			return nil
		}
		seen[dir] = true
		dep, err := readProject(tree, dir)
		if err != nil {
			return fmt.Errorf("%s %w", path.Join(dir, "pyproject.toml"), err)
		}
		if dep != nil {
			queue = append(queue, project{dir, dep})
		}
		return nil
	}

	members, err := workspaceMembers(tree, module, doc)
	if err != nil {
		return nil, err
	}
	for _, member := range members {
		if err := add(member, fmt.Sprintf("%s: [tool.uv.workspace] member %q", path.Join(module, "pyproject.toml"), modulePath(module, member))); err != nil {
			return nil, err
		}
		if err := visit(member); err != nil {
			return nil, err
		}
	}
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		file := path.Join(current.dir, "pyproject.toml")
		sources := table(table(table(current.doc, "tool"), "uv"), "sources")
		names := make([]string, 0, len(sources))
		for name := range sources {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			for _, source := range sourceTables(sources[name]) {
				p, ok := source["path"].(string)
				if !ok {
					continue
				}
				if path.IsAbs(p) {
					return nil, fmt.Errorf("%s: [tool.uv.sources] %s path %q is absolute; a module loads only files inside its workspace", file, name, p)
				}
				dir := path.Join(current.dir, p)
				if leadingParents(dir) > 0 {
					return nil, fmt.Errorf("%s: [tool.uv.sources] %s path %q leaves the workspace root", file, name, p)
				}
				if err := add(dir, fmt.Sprintf("%s: [tool.uv.sources] %s path %q", file, name, p)); err != nil {
					return nil, err
				}
				if err := visit(dir); err != nil {
					return nil, err
				}
			}
		}
	}

	locked, err := lockedSources(tree, module)
	if err != nil {
		return nil, err
	}
	for _, rooted := range locked {
		if err := add(rooted, fmt.Sprintf("%s: %q", path.Join(module, "uv.lock"), modulePath(module, rooted))); err != nil {
			return nil, err
		}
	}
	return resources, nil
}

// uv reads a workspace member outside the module directory like a path
// source, so it is derived as one.
func workspaceMembers(tree, module string, doc map[string]any) ([]string, error) {
	workspace := table(table(table(doc, "tool"), "uv"), "workspace")
	file := path.Join(module, "pyproject.toml")
	matching := func(key, pattern string) ([]string, error) {
		if leadingParents(path.Join(module, pattern)) > 0 {
			return nil, fmt.Errorf("%s: [tool.uv.workspace] %s %q leaves the workspace root", file, key, pattern)
		}
		found, err := filepath.Glob(filepath.Join(tree, module, pattern))
		if err != nil {
			return nil, fmt.Errorf("%s: [tool.uv.workspace] %s %q: %w", file, key, pattern, err)
		}
		var dirs []string
		for _, f := range found {
			rel, err := filepath.Rel(tree, f)
			if err != nil {
				return nil, err
			}
			dirs = append(dirs, filepath.ToSlash(rel))
		}
		sort.Strings(dirs)
		return dirs, nil
	}
	excluded := map[string]bool{}
	for _, pattern := range stringList(workspace["exclude"]) {
		dirs, err := matching("exclude", pattern)
		if err != nil {
			return nil, err
		}
		for _, dir := range dirs {
			excluded[dir] = true
		}
	}
	var members []string
	for _, pattern := range stringList(workspace["members"]) {
		dirs, err := matching("members", pattern)
		if err != nil {
			return nil, err
		}
		for _, dir := range dirs {
			if excluded[dir] {
				continue
			}
			if _, err := os.Stat(filepath.Join(tree, dir, "pyproject.toml")); err == nil {
				members = append(members, dir)
			}
		}
	}
	return members, nil
}

func stringList(value any) []string {
	list, _ := value.([]any)
	var out []string
	for _, v := range list {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// A fresh uv.lock holds every path source uv followed, so it catches what the
// walk missed.
func lockedSources(tree, module string) ([]string, error) {
	file := path.Join(module, "uv.lock")
	data, err := os.ReadFile(filepath.Join(tree, file))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	lock, err := load(data)
	if err != nil {
		return nil, fmt.Errorf("%s is not valid TOML: %w", file, err)
	}
	packages, _ := lock["package"].([]any)
	var rooted []string
	for _, p := range packages {
		pkg, _ := p.(map[string]any)
		source, _ := pkg["source"].(map[string]any)
		name, _ := pkg["name"].(string)
		for _, key := range []string{"editable", "directory", "path", "virtual"} {
			local, ok := source[key].(string)
			if !ok {
				continue
			}
			if path.IsAbs(local) {
				return nil, fmt.Errorf("%s: %s is at %q, an absolute path; a module loads only files inside its workspace", file, name, local)
			}
			dir := path.Join(module, local)
			if leadingParents(dir) > 0 {
				return nil, fmt.Errorf("%s: %s at %q leaves the workspace root", file, name, local)
			}
			rooted = append(rooted, dir)
		}
	}
	sort.Strings(rooted)
	return rooted, nil
}

// A [tool.uv.sources] entry is an array of tables when its sources differ by
// marker.
func sourceTables(entry any) []map[string]any {
	switch v := entry.(type) {
	case map[string]any:
		return []map[string]any{v}
	case []any:
		var tables []map[string]any
		for _, item := range v {
			if t, ok := item.(map[string]any); ok {
				tables = append(tables, t)
			}
		}
		return tables
	}
	return nil
}

// A path source that is a wheel has no pyproject.toml and nothing to follow.
// The error goes after the file's name and keeps the build reader's wording,
// so the user reads one message for one fault.
func readProject(tree, dir string) (map[string]any, error) {
	data, err := os.ReadFile(filepath.Join(tree, dir, "pyproject.toml"))
	if errors.Is(err, fs.ErrNotExist) || errors.Is(err, syscall.ENOTDIR) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	doc, err := load(data)
	if err != nil {
		return nil, fmt.Errorf("is not valid TOML: %w", err)
	}
	return doc, nil
}

func within(module, rooted string) (string, bool) {
	if module == "." {
		return rooted, leadingParents(rooted) == 0
	}
	if rooted == module {
		return ".", true
	}
	inner, ok := strings.CutPrefix(rooted, module+"/")
	return inner, ok
}

func modulePath(module, rooted string) string {
	rel, err := filepath.Rel(module, rooted)
	if err != nil {
		return rooted
	}
	return filepath.ToSlash(rel)
}

func leadingParents(cleaned string) int {
	n := 0
	for _, segment := range strings.Split(cleaned, "/") {
		if segment != ".." {
			break
		}
		n++
	}
	return n
}
