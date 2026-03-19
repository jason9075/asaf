{
  description = "ASAF — Discord personality profile extraction pipeline";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python312;

        pythonEnv = python.withPackages (ps: with ps; [
          # LLM clients
          anthropic
          openai

          # Data / DB
          # sqlite3 is built-in

          # Utilities
          python-dotenv
          tqdm
          rich

          # Dev
          mypy
          ruff
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.just
            pkgs.sqlite
          ];

          shellHook = ''
            echo "ASAF dev shell ready (Python $(python --version))"
            [ -f .env ] && export $(grep -v '^#' .env | xargs) && echo ".env loaded"
          '';
        };
      });
}
