# claude-multi v2 Home Manager module: pinned CLIProxyAPI (both local patches
# unchanged), the v2 package, and the user gateway service. Claude Code stays
# an external user installation (native-contract record), never packaged here.
{ pkgs, inputs, ... }:

let
  cliProxyApi =
    inputs.llm-agents.packages.${pkgs.stdenv.hostPlatform.system}.cli-proxy-api.overrideAttrs
      (oldAttrs: {
        patches = (oldAttrs.patches or [ ]) ++ [
          ../cli-proxy-api-loopback-oauth.patch
          ../cli-proxy-api-kimi-claude-compat.patch
          ../cli-proxy-api-opus-5-model.patch
        ];
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
