package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"text/template"
)

func main() {
	if err := run(os.Args[1:], engineTypeName); err != nil {
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

// engineTypeName is the module's name in PascalCase as the engine spells it
// (formatIdentifiers), which the template names the main class after: the
// engine knows where the name's words are. It asks the Dagger session the
// command runs in as a nested client.
func engineTypeName(moduleName string) (string, error) {
	port, token := os.Getenv("DAGGER_SESSION_PORT"), os.Getenv("DAGGER_SESSION_TOKEN")
	if port == "" || token == "" {
		return "", errors.New("no Dagger session to name the module's class with: run render-template as a nested client")
	}
	body, err := json.Marshal(map[string]any{
		"query":     `query($names: [String!]!) { formatIdentifiers(names: $names, casing: PASCAL) }`,
		"variables": map[string]any{"names": []string{moduleName}},
	})
	if err != nil {
		return "", err
	}
	req, err := http.NewRequest(http.MethodPost, "http://127.0.0.1:"+port+"/query", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.SetBasicAuth(token, "")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	var result struct {
		Data struct {
			FormatIdentifiers []string `json:"formatIdentifiers"`
		} `json:"data"`
		Errors []struct {
			Message string `json:"message"`
		} `json:"errors"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("format the module's name: HTTP %s: %w", resp.Status, err)
	}
	if len(result.Errors) > 0 {
		return "", fmt.Errorf("format the module's name %q: %s", moduleName, result.Errors[0].Message)
	}
	if len(result.Data.FormatIdentifiers) != 1 {
		return "", fmt.Errorf("format the module's name %q: got %v", moduleName, result.Data.FormatIdentifiers)
	}
	return result.Data.FormatIdentifiers[0], nil
}

func run(args []string, typeName func(string) (string, error)) error {
	if len(args) != 3 {
		return fmt.Errorf("usage: render-template MODULE_NAME TEMPLATE_DIR OUT_DIR")
	}

	moduleName := args[0]
	templateDir := args[1]
	outDir := args[2]
	moduleType, err := typeName(moduleName)
	if err != nil {
		return err
	}
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
