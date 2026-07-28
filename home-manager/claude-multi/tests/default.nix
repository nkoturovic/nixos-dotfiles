# Offline unittest derivation for the claude-multi v2 suite (Phase 4 scaffold).
# Runs the complete current suite in a Nix sandbox against this tree.
# Default pkgs resolves the locked nixpkgs from the repository flake.lock.
{
  pkgs ?
    import
      (
        let
          lock = builtins.fromJSON (builtins.readFile ../../../flake.lock);
          node = lock.nodes.nixpkgs.locked;
        in
        fetchTarball {
          url = "https://github.com/${node.owner}/${node.repo}/archive/${node.rev}.tar.gz";
          sha256 = node.narHash;
        }
      )
      { },
}:

let
  package = pkgs.callPackage ../package.nix { };
  # The gateway patch manifest is the single source of truth for which patch
  # files the module applies; GatewayManifestConsistencyTests resolves them
  # next to the package tree (CATALOG_ROOT.parent), so the sandbox must stage
  # the same layout. Symlinks break Path.resolve() in the tests — copy.
  manifestPatches =
    (builtins.fromJSON (builtins.readFile ../catalog/gateway.json)).gateway.patches;
in
pkgs.runCommand "claude-multi-tests"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    env.PYTHONDONTWRITEBYTECODE = "1";
    tree = ./..;
    inherit package;
    patchFiles = map (name: ../.. + "/${name}") manifestPatches;
  }
  ''
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    staged="$TMPDIR/staged/home-manager"
    mkdir -p "$staged"
    cp -r "$tree" "$staged/claude-multi"
    chmod -R u+w "$staged/claude-multi"
    for p in $patchFiles; do
      cp "$p" "$staged/$(stripHash "$p")"
    done
    export PYTHONPATH="$staged/claude-multi/src:$staged/claude-multi/tests"
    cd "$TMPDIR"
    python3 -m unittest discover -s "$staged/claude-multi/tests" -p 'test_*.py'
    "$package/bin/claude-multi" --version
    "$package/bin/claude-gateway" --version
    "$package/bin/claude-multi-proxy" --version
    touch $out
  ''
