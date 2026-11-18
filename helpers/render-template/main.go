package main

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"text/template"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

var nonAlphanumeric = regexp.MustCompile(`[^a-z0-9]+`)

// packageName must match packageNameFor in runtime/build.dang: the runtime
// imports that package, and uv_build expects the same directory.
func packageName(moduleName string) string {
	project := strings.Trim(nonAlphanumeric.ReplaceAllString(strings.ToLower(moduleName), "-"), "-")
	return strings.ReplaceAll(project, "-", "_")
}

// run renders the template for a module. MODULE_TYPE names the main class:
// the module's type name as the engine will name the module, which the caller
// asks the engine for.
func run(args []string) error {
	if len(args) != 4 {
		return fmt.Errorf("usage: render-template MODULE_NAME MODULE_TYPE TEMPLATE_DIR OUT_DIR")
	}

	moduleName := args[0]
	moduleType := args[1]
	templateDir := args[2]
	outDir := args[3]
	data := map[string]string{
		"ModuleName":    moduleName,
		"ModuleProject": strings.ToLower(moduleName),
		"ModuleType":    moduleType,
		"ModulePackage": packageName(moduleName),
	}

	return filepath.WalkDir(templateDir, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(templateDir, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}

		dstRel := strings.TrimSuffix(rel, ".tmpl")
		if strings.Contains(dstRel, "{{") {
			pathTmpl, err := template.New("path-" + rel).Parse(dstRel)
			if err != nil {
				return err
			}
			var pathBuf bytes.Buffer
			if err := pathTmpl.Execute(&pathBuf, data); err != nil {
				return err
			}
			dstRel = pathBuf.String()
		}
		dst := filepath.Join(outDir, dstRel)
		if entry.IsDir() {
			return os.MkdirAll(dst, 0o755)
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("template symlinks are not supported: %s", rel)
		}
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return err
		}

		contents, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if !strings.HasSuffix(rel, ".tmpl") {
			return os.WriteFile(dst, contents, 0o644)
		}

		var buf bytes.Buffer
		tmpl, err := template.New(rel).Parse(string(contents))
		if err != nil {
			return err
		}
		if err := tmpl.Execute(&buf, data); err != nil {
			return err
		}
		return os.WriteFile(dst, buf.Bytes(), 0o644)
	})
}
