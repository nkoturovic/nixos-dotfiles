{
  description = "System configuration";

  inputs = {
    nixpkgs.url = "nixpkgs/nixos-21.11";
    home-manager.url = "github:nix-community/home-manager/release-21.11";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { nixpkgs, home-manager, ... }: 
  let 
      system = "x86_64-linux";
      pkgs = import nixpkgs {
          inherit system;
          config = { allowUnfree = true; };
      };
      lib = nixpkgs.lib;
  in {
      homeManagerConfigurations = {
          # username = opts
          kotur = home-manager.lib.homeManagerConfiguration {
	     inherit system pkgs;
             username = "kotur";
             homeDirectory = "/home/kotur";
             configuration = {
                imports = [
                   ./users/kotur/home.nix
                ];
             };
	  };
      };

      nixosConfigurations = {
	  # hostname = opts
          kotur-pc = lib.nixosSystem {
	      inherit system;
              modules = [
                  ./system/configuration.nix
              ];
          };
      };
  };
}
