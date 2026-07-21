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

pkgs.runCommand "claude-multi-tests"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    env.PYTHONDONTWRITEBYTECODE = "1";
    tree = ./..;
  }
  ''
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export PYTHONPATH="$tree/src:$tree/tests"
    cd "$TMPDIR"
    python3 -m unittest discover -s "$tree/tests" -p 'test_*.py'
    touch $out
  ''
