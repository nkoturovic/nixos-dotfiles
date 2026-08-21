# claude-multi v2 Home Manager module: pinned CLIProxyAPI 7.2.80 with the
# four local gateway patches (loopback OAuth bind, Kimi/Claude compat,
# opus-5 registry entry, non-Claude cache-retention boundary — the first
# three unchanged from 2.19.0, the fourth new in 2.20.0), the v2 package, and
# the user gateway service. Claude Code stays an external user installation
# (native-contract record), never packaged here.
{ pkgs, inputs, ... }:

let
  cliProxyApi =
    inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system}.cli-proxy-api.overrideAttrs
      (oldAttrs: {
        patches = (oldAttrs.patches or [ ]) ++ [
          ../cli-proxy-api-loopback-oauth.patch
          ../cli-proxy-api-kimi-claude-compat.patch
          ../cli-proxy-api-opus-5-model.patch
          ../cli-proxy-api-non-claude-cache-retention.patch
        ];
        # Network-free regression gate for the patched source: the upstream
        # checkPhase only tests subPackages (cmd/server), so the retention
        # sanitizer suite would never run in the sandboxed build. Run the
        # executor package tests explicitly — loopback fakes and injected
        # round-trippers only, vendored modules, GOPROXY=off; no external
        # network. The echoed marker proves execution in build logs.
        doCheck = true;
        postCheck = (oldAttrs.postCheck or "") + ''
          echo "cli-proxy-api: executor regression gate (go test ./internal/runtime/executor)"
          go test ./internal/runtime/executor
        '';
      });

  claudeMulti = pkgs.callPackage ./package.nix {
    cliProxyApiBin = "${cliProxyApi}/bin/cli-proxy-api";
  };
in
{
  home.packages = [ claudeMulti cliProxyApi ];

  # claude-multi local model gateway (loopback-only, secrets rendered at runtime)
  systemd.user.services.cli-proxy-api = {
    Unit = {
      Description = "claude-multi local model gateway";
      After = [ "network-online.target" ];
      Wants = [ "network-online.target" ];
    };
    Service = {
      Type = "simple";
      ExecStart = "${claudeMulti}/bin/claude-multi-proxy run";
      Restart = "on-failure";
      RestartSec = "5s";
    };
    Install.WantedBy = [ "default.target" ];
  };
}
