// Runtime module for the Python SDK

package main

import (
	"context"
	"fmt"
	"path"

	"python-sdk-runtime/internal/dagger"
)

const (
	ModSourceDirPath      = "/src"
	RuntimeExecutablePath = "/runtime"
	GenDir                = "sdk"
	SDKGenPath            = "src/dagger/client/gen.py"
	UserGenPath           = "src/dagger_gen.py"
	SchemaPath            = "/schema.json"
	VenvPath              = "/opt/venv"
	ProjectCfg            = "pyproject.toml"
	PipCompileLock        = "requirements.lock"
	UvLock                = "uv.lock"
)

// UserConfig is the custom user configuration that users can add to their pyproject.toml.
//
// For example:
// ```toml
// [tool.dagger]
// use-uv = false
// ```
type UserConfig struct {
	// BaseImage is the image reference to use for the base container.
	BaseImage string `toml:"base-image"`

	// UseUv is for choosing the faster uv tool instead of pip to install packages.
	UseUv bool `toml:"use-uv"`

	// UvVersion is the version of the uv tool to use.
	//
	// By default, it's pinned to a specific version in each dagger version.
	UvVersion string `toml:"uv-version"`
}

// sdkSourceFilter is what a contextual `+ignore` used to do for sdkSourceDir:
// keep the library and the code generator, and nothing else, out of whatever
// happens to sit next to them. It has to be applied by hand now that the
// directory comes from the module's own source.
var sdkSourceFilter = []string{
	"**",
	"!pyproject.toml",
	"!uv.lock",
	"!src/**/*.py",
	"!src/**/*.typed",
	"!codegen/pyproject.toml",
	"!codegen/**/*.py",
	"!LICENSE",
	"!README.md",
	"!dist/*",
}

func New(
	// Directory with the Python SDK source code. Defaults to the copy vendored
	// in this module; extension SDKs can pass their own.
	//
	// NB: deliberately not a contextual (+defaultPath) argument. Contextual
	// resolution is redirected to a Workspace bound into the context when there
	// is one, which for a runtime loaded by ref means the *consuming* workspace
	// rather than this module. currentModule().source() has no such ambiguity.
	// +optional
	sdkSourceDir *dagger.Directory,
) (*PythonSdkRuntime, error) {
	if sdkSourceDir == nil {
		sdkSourceDir = dag.CurrentModule().
			Source().
			Directory("sdk").
			Filter(dagger.DirectoryFilterOpts{Exclude: sdkSourceFilter})
	}
	d, err := NewDiscovery(UserConfig{
		UseUv: true,
	})
	if err != nil {
		return nil, err
	}
	return &PythonSdkRuntime{
		Discovery:    d,
		SdkSourceDir: sdkSourceDir,
		Container:    dag.Container(),
		// TODO: remove the following when we no longer vendor every time
		VendorPath: GenDir,
	}, nil
}

// Functions for building the runtime module for the Python SDK.
//
// The server interacts directly with the ModuleRuntime and Codegen functions.
// The others were built to be composable and chainable to facilitate the
// creation of extension modules (custom SDKs that depend on this one).
type PythonSdkRuntime struct {
	// Directory with the Python SDK source code
	SdkSourceDir *dagger.Directory

	// Resulting container after each composing step
	Container *dagger.Container

	// Whether the module runtime should run in debug mode.
	Debug bool

	// The original module's name
	ModName string

	// The normalized python distribution package name (in pyproject.toml)
	ProjectName string

	// The normalized python import package name (in the filesystem)
	PackageName string

	// The normalized main object name in Python
	MainObjectName string

	// The source needed to load and run a module
	ModSource *dagger.ModuleSource

	// ContextDir is a copy of the context directory from the module source
	//
	// We add files to this directory, always joining paths with the source's
	// subpath. We could use modSource.Directory("") for that if it was read-only,
	// but since we have to mount the context directory in the end, rather than
	// mounting the context dir and then mounting the forked source dir on top,
	// we fork the context dir instead so there's only one mount in the end.
	ContextDir *dagger.Directory

	// ContextDirPath is a unique host path for the module being loaded
	//
	// HACK: this property is computed as a unique value for a ModuleSource to
	// provide a unique path on the filesystem. This is because the uv cache
	// uses hashes of source paths - so we need to have something unique, or we
	// can get very real conflicts in the uv cache.
	ContextDirPath string

	// Relative path from the context directory to the source directory
	SubPath string

	// Relative path to vendor client library into
	VendorPath string

	// True if the module is new and we need to create files from the template
	//
	// It's assumed that this is the case if there's no pyproject.toml file.
	IsInit bool

	// TrustedSource is true when building the module rather than generating
	// its code: the committed vendored sdk is kept as-is (not stripped and
	// re-vendored) and the committed lock file is trusted rather than
	// re-resolved. Set by ModuleRuntime and never by Codegen, which is the
	// one path that legitimately rewrites those files.
	// +private
	TrustedSource bool

	// Discovery holds the logic for getting more information from the target module.
	// +private
	Discovery *Discovery
}

// Generated code for the Python module
func (m *PythonSdkRuntime) Codegen(
	ctx context.Context,
	modSource *dagger.ModuleSource,
	introspectionJSON *dagger.File,
) (*dagger.GeneratedCode, error) {
	m, err := m.Common(ctx, modSource, introspectionJSON)
	if err != nil {
		return nil, err
	}

	ignorePaths := []string{".venv", "**/__pycache__"}
	genPaths := []string{
		// TODO: uncomment when we start generating client bindings outside the library
		// UserGenPath,
	}

	if m.VendorPath != "" {
		ignorePaths = append(ignorePaths, m.VendorPath)
		genPaths = []string{m.VendorPath + "/**"}
	}

	return dag.
		GeneratedCode(m.Container.Directory(m.ContextDirPath)).
		WithVCSGeneratedPaths(genPaths).
		WithVCSIgnoredPaths(ignorePaths), nil
}

// Container for executing the Python module runtime
//
// The container is built from the module's committed generated files: no SDK
// vendoring, no client bindings generation, no lock update. Dependencies are
// still installed — the language-level assemble step, like the Go SDK still
// running go build on its trusted path.
//
// introspectionJSON is declared and unused on purpose. Its optionality is the
// signal the engine reads (RuntimeTrustsCommittedFiles) to decide it may skip
// runtime codegen and omit the argument entirely, which it does for every
// dagger-module.toml module — the only kind this runtime is meant to serve.
// A legacy dagger.json module that named this runtime explicitly would still
// be handed one; it is ignored, so such a module works if it has committed its
// generated files and fails the check below with an actionable error if not.
func (m *PythonSdkRuntime) ModuleRuntime(
	ctx context.Context,
	modSource *dagger.ModuleSource,
	// +optional
	introspectionJSON *dagger.File,
) (*dagger.Container, error) {
	m.TrustedSource = true

	if _, err := m.Load(ctx, modSource); err != nil {
		return nil, err
	}
	if m.IsInit {
		return nil, fmt.Errorf("module %q has no source to trust; run `dagger generate` and commit the generated files", m.ModName)
	}
	if err := m.requireGeneratedFiles(ctx); err != nil {
		return nil, err
	}
	if _, err := m.WithBase(); err != nil {
		return nil, err
	}
	runtime := m.
		withRuntimeScript().
		WithSource().
		WithInstall()
	ctr := runtime.Container
	if runtime.Debug {
		ctr = ctr.Terminal()
	}
	return ctr, nil
}

// requireGeneratedFiles verifies the module's committed generated files are
// present. Called only on the trusted path; the codegen path regenerates
// them so the check would be redundant.
func (m *PythonSdkRuntime) requireGeneratedFiles(ctx context.Context) error {
	// The generated client bindings live inside the vendored SDK, or at
	// UserGenPath when the library isn't vendored.
	required := []string{UserGenPath}
	if m.VendorPath != "" {
		required = []string{
			path.Join(m.VendorPath, ProjectCfg),
			path.Join(m.VendorPath, SDKGenPath),
		}
	}
	for _, rel := range required {
		exists, err := m.Source().Exists(ctx, rel)
		if err != nil {
			return fmt.Errorf("check generated file %q: %w", rel, err)
		}
		if exists {
			continue
		}
		return fmt.Errorf(
			"module %q: generated file %q is missing; run `dagger generate` and commit the generated files",
			m.ModName, rel)
	}
	return nil
}

// Common steps for the Codegen function
//
// ModuleRuntime no longer shares this: it builds from committed files and so
// runs neither WithSDK nor WithUpdates. Kept exported and granular because
// extension SDKs compose it.
func (m *PythonSdkRuntime) Common(
	ctx context.Context,
	modSource *dagger.ModuleSource,
	// +optional
	introspectionJSON *dagger.File,
) (*PythonSdkRuntime, error) {
	// The following functions were built to be composable in a granular way,
	// to allow a custom SDK to depend on this one and hook into before or
	// after major steps in the process. For example, you can get the base
	// container, add system packages, use the new one with `WithContainer`,
	// and then continue with the rest of the steps. Without this, you'd need
	// to copy the entire function and modify it.

	// NB: In extension modules, Load is chainable.
	_, err := m.Load(ctx, modSource)
	if err != nil {
		return nil, err
	}
	// Upstream seeded a pyproject.toml from a template here. Templates live in
	// this repository's initModule now, so say what is missing rather than
	// letting `uv lock` fail on an empty directory further down.
	if m.IsInit {
		return nil, fmt.Errorf(
			"module %q has no Python source to generate from; create it with `dagger module init python`",
			m.ModName)
	}
	_, err = m.WithBase()
	if err != nil {
		return nil, err
	}
	return m.
		WithSDK(introspectionJSON).
		withRuntimeScript().
		WithSource().
		WithUpdates(), nil
}

// Get all the needed information from the module's metadata and source files
func (m *PythonSdkRuntime) Load(ctx context.Context, modSource *dagger.ModuleSource) (*PythonSdkRuntime, error) {
	m.ModSource = modSource
	m.ContextDir = modSource.ContextDirectory()
	debug, err := modSource.SDK().Debug(ctx)
	if err != nil {
		return nil, fmt.Errorf("runtime module load: %w", err)
	}
	m.Debug = debug

	if err := m.Discovery.Load(ctx, m); err != nil {
		return nil, fmt.Errorf("runtime module load: %w", err)
	}

	return m, nil
}

// Initialize the base Python container
//
// Workdir is set to the module's source directory.
func (m *PythonSdkRuntime) WithBase() (*PythonSdkRuntime, error) {
	baseAddr := m.getImage(BaseImageName).String()

	// NB: Adding env vars with container images that were pulled allows
	// modules to reuse them for performance benefits.
	m.Container = dag.Container().
		// Base Python
		From(baseAddr).
		// This var is informational only, in case it's useful in a module.
		WithEnvVariable("DAGGER_BASE_IMAGE", baseAddr).
		WithEnvVariable("PYTHONUNBUFFERED", "1").
		WithEnvVariable("PIP_DISABLE_PIP_VERSION_CHECK", "1").
		WithEnvVariable("PIP_ROOT_USER_ACTION", "ignore").
		// Uv
		With(m.uv()).
		WithEnvVariable("UV_SYSTEM_PYTHON", "1").
		WithEnvVariable("UV_LINK_MODE", "copy").
		WithEnvVariable("UV_NATIVE_TLS", "1").
		WithEnvVariable("UV_PROJECT_ENVIRONMENT", "/opt/venv")

	if !m.UseUv() {
		m.Container = m.Container.WithMountedCache("/root/.cache/pip", dag.CacheVolume("modpython-pip"))
	}
	if m.IndexURL() != "" {
		m.Container = m.Container.WithEnvVariable("UV_INDEX_URL", m.IndexURL())
	}
	if m.ExtraIndexURL() != "" {
		m.Container = m.Container.WithEnvVariable("UV_EXTRA_INDEX_URL", m.ExtraIndexURL())
	}

	return m, nil
}

func (m *PythonSdkRuntime) uv() dagger.WithContainerFunc {
	uvImage := m.getImage(UvImageName)

	return func(ctr *dagger.Container) *dagger.Container {
		var bins *dagger.Directory
		// Use bundled uv binaries if version wasn't overridden.
		if m.Discovery.SdkHasFile("dist/uv") && uvImage.Equal(m.Discovery.DefaultImages[UvImageName]) {
			bins = m.SdkSourceDir.Directory("dist")
		} else {
			bins = dag.Container().From(uvImage.String()).Rootfs()
		}

		return ctr.
			WithMountedFile("/usr/local/bin/uv", bins.File("uv")).
			WithMountedFile("/usr/local/bin/uvx", bins.File("uvx")).
			WithMountedCache("/root/.cache/uv", dag.CacheVolume("modpython-uv")).
			// These are informational only, to be leveraged by the target module if needed.
			WithEnvVariable("DAGGER_UV_IMAGE", uvImage.String()).
			WithEnvVariable("DAGGER_UV_VERSION", uvImage.Tag())
	}
}

// withRuntimeScript mounts the runtime entrypoint script and sets it as the
// container entrypoint.
func (m *PythonSdkRuntime) withRuntimeScript() *PythonSdkRuntime {
	m.Container = m.Container.
		WithFile(
			RuntimeExecutablePath,
			dag.CurrentModule().Source().File("runtime.py"),
			dagger.ContainerWithFileOpts{Permissions: 0o755},
		).
		WithEntrypoint([]string{RuntimeExecutablePath})
	return m
}

// Add the SDK package to the source directory
//
// This includes regenerating the client bindings for the current API schema
// (codegen).
func (m *PythonSdkRuntime) WithSDK(introspectionJSON *dagger.File) *PythonSdkRuntime {
	if m.VendorPath != "" {
		src := m.SdkSourceDir
		// If not vendoring we don't care to remove this
		if m.Discovery.SdkHasFile("dist/") {
			src = src.WithoutDirectory("dist")
		}
		m.AddDirectory(m.VendorPath, src)
	}

	// Allow empty introspection to facilitate debugging the container with a
	// `dagger call module-runtime terminal` command.
	if introspectionJSON != nil {
		ctr := m.Container
		cmd := []string{"codegen"}

		// When not using the bundled codegen executable we can revert to executing directly
		if m.Discovery.SdkHasFile("dist/codegen") {
			ctr = ctr.
				WithMountedCache("/root/.shiv", dag.CacheVolume("shiv")).
				WithMountedFile("/usr/local/bin/codegen", m.SdkSourceDir.File("dist/codegen"))
		} else {
			ctr = ctr.
				WithWorkdir("/sdk").
				WithMountedDirectory("", m.SdkSourceDir)
			cmd = []string{
				"uv", "run", "--isolated", "--frozen", "--package", "codegen",
				"python", "-m", "codegen",
			}
		}

		genFile := ctr.
			// mounted schema as late as possible because it varies more often
			WithMountedFile(SchemaPath, introspectionJSON).
			WithExec(append(cmd, "generate", "-i", SchemaPath, "-o", "/gen.py")).
			File("/gen.py")

		genPath := UserGenPath

		// For now, patch vendored client library with generated bindings.
		// TODO: Always generate outside library, even if vendored.
		if m.VendorPath != "" {
			genPath = path.Join(m.VendorPath, SDKGenPath)
		}

		m.AddFile(genPath, genFile)
	}

	return m
}

// Add the module's source code
func (m *PythonSdkRuntime) WithSource() *PythonSdkRuntime {
	m.Container = m.Container.
		WithWorkdir(path.Join(m.ContextDirPath, m.SubPath)).
		WithMountedDirectory(m.ContextDirPath, m.ContextDir).
		// These are added as late as possible  to avoid cache invalidation
		// between different modules. It may be used by the runtime entrypoint
		// so only needed in ModuleRuntime but added here so that extension
		// modules get it for free since they need to reimplement ModuleRuntime.
		// It's ok since the previous layer is already dependent on the target
		// module's sources.
		WithEnvVariable("DAGGER_MODULE", m.ModName).
		WithEnvVariable("DAGGER_DEFAULT_PYTHON_PACKAGE", m.PackageName).
		WithEnvVariable("DAGGER_MAIN_OBJECT", m.MainObjectName)
	return m
}

// Make any updates to current source
func (m *PythonSdkRuntime) WithUpdates() *PythonSdkRuntime {
	if !m.UseUv() {
		return m
	}

	ctr := m.Container
	d := m.Discovery

	// Update lock file but without upgrading dependencies.
	switch {
	case m.UseUvLock():
		// Support uv.lock. Takes precedence.
		// Always update if uv.lock exists, but only create a new uv.lock
		// if init and there's not already a requirements.lock.
		ctr = ctr.WithExec([]string{"uv", "lock"})

	case d.HasFile(PipCompileLock) && !m.IsInit:
		// Support requirements.lock (legacy).
		args := []string{
			"uv", "pip", "compile", "-q", "--universal",
			"-o", PipCompileLock,
			ProjectCfg,
		}

		if m.VendorPath != "" {
			args = append(args, path.Join(m.VendorPath, ProjectCfg))
		}

		ctr = ctr.WithExec(args)
	}

	m.Container = ctr

	return m
}

// Install the module's package and dependencies
func (m *PythonSdkRuntime) WithInstall() *PythonSdkRuntime {
	// NB: Only enable bytecode compilation in `dagger call`
	// (not `dagger init/develop`), to avoid having to remove the .pyc files
	// before exporting the module back to the host.
	ctr := m.Container.WithEnvVariable("UV_COMPILE_BYTECODE", "1")

	// Support uv.lock for simple and fast project management workflow.
	if m.UseUvLock() {
		syncArgs := []string{"uv", "sync", "--no-dev"}
		if m.TrustedSource {
			// Trust the committed lockfile: fail loudly if it's stale
			// instead of silently re-resolving.
			syncArgs = append(syncArgs, "--locked")
		}
		// While best practice is to sync dependencies first with only pyproject.toml and
		// uv.lock, user projects can have more required files for a minimally successful
		// `uv sync --no-install-project --no-dev`.
		// Besides, uv is fast enough that's not too bad to skip this optimization.
		m.Container = ctr.
			WithExec(syncArgs).
			// Activate virtualenv to avoid having to prepend `uv run` to the entrypoint.
			WithEnvVariable("VIRTUAL_ENV", "$UV_PROJECT_ENVIRONMENT", dagger.ContainerWithEnvVariableOpts{
				Expand: true,
			}).
			WithEnvVariable("PATH", "$VIRTUAL_ENV/bin:$PATH", dagger.ContainerWithEnvVariableOpts{
				Expand: true,
			})
		return m
	}

	// Fallback to pip-compile workflow (legacy).
	install := []string{"pip", "install", "-e", "./sdk", "-e", "."}
	check := []string{"pip", "check"}

	// uv has a compatible API with pip
	if m.UseUv() {
		// Support requirements.lock.
		if m.Discovery.HasFile(PipCompileLock) {
			// If there's a lock file, we assume that all the dependencies are
			// included in it so we can avoid resolving for them to get a faster
			// install.
			install = append(install, "--no-deps", "-r", PipCompileLock)
		}
		// pip compiles by default, but not uv
		install = append([]string{"uv"}, install...)
		check = append([]string{"uv"}, check...)
	}

	m.Container = ctr.
		WithExec(install).
		WithExec(check)

	return m
}
