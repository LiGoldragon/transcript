{
  description = "CLI shim for searching Claude Code harness transcripts";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
    in {
      packages = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in {
          default = pkgs.writeShellApplication {
            name = "transcript";
            runtimeInputs = [ pkgs.python3 ];
            text = ''
              exec python3 ${./transcript.py} "$@"
            '';
          };
        }
      );
    };
}
