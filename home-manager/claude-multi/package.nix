# claude-multi v2 standalone package (Phase 4 scaffold; Phase 5 wires the module).
# Builds the pinned-Python v2 tree with no third-party Python dependencies.
# Default pkgs resolves the locked nixpkgs from the repository flake.lock,
# so plain `nix-build --no-out-link package.nix` works without flake evaluation.
# cliProxyApiBin pins the CLIProxyAPI path into the claude-multi-proxy wrapper
# when the Home Manager module supplies it; standalone builds resolve at runtime.
{
  pkgs ?
    import
      (
        let
          lock = builtins.fromJSON (builtins.readFile ../../flake.lock);
          node = lock.nodes.nixpkgs.locked;
        in
        fetchTarball {
          url = "https://github.com/${node.owner}/${node.repo}/archive/${node.rev}.tar.gz";
          sha256 = node.narHash;
        }
      )
      { },
  cliProxyApiBin ? null,
}:

let
  python = pkgs.python3;
  proxyFlag =
    pkgs.lib.optionalString (cliProxyApiBin != null)
      ''--set CLAUDE_MULTI_PROXY_BIN "${cliProxyApiBin}"'';
in
pkgs.stdenv.mkDerivation {
  pname = "claude-multi";
  version = (builtins.fromJSON (builtins.readFile ./version.json)).launcher_version;
  # Test-run __pycache__/.pyc artifacts must never reach the store: they would
  # both pollute the share tree and churn the source hash on every test run.
  src = pkgs.lib.cleanSourceWith {
    src = ./.;
    filter =
      path: _type:
      let
        base = baseNameOf (toString path);
      in
      base != "__pycache__" && !(pkgs.lib.hasSuffix ".pyc" base);
  };
  nativeBuildInputs = [ pkgs.makeWrapper ];
  dontBuild = true;
  installPhase = ''
    runHook preInstall

    mkdir -p $out/share/claude-multi $out/bin
    cp -r catalog schemas src settings.json version.json $out/share/claude-multi/
    cp -r bin $out/share/claude-multi/bin

    for entry in claude-multi claude-multi-dev claude-gateway; do
      makeWrapper ${python}/bin/python3 $out/bin/$entry \
        --set PYTHONPATH "$out/share/claude-multi/src" \
        --set CLAUDE_MULTI_ASSETS "$out/share/claude-multi" \
        --set CLAUDE_MULTI_HOOK_COMMAND "$out/bin/claude-multi" \
        --add-flags "$out/share/claude-multi/bin/$entry"
    done

    makeWrapper ${python}/bin/python3 $out/bin/claude-multi-proxy \
      --set PYTHONPATH "$out/share/claude-multi/src" \
      --set CLAUDE_MULTI_ASSETS "$out/share/claude-multi" \
      ${proxyFlag} \
      --add-flags "$out/share/claude-multi/bin/claude-multi-proxy"

    runHook postInstall
  '';
  meta = {
    description = "claude-multi v2 composition compiler and onboarding tools";
    mainProgram = "claude-multi";
  };
}
