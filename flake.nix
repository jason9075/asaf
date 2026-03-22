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

          # Discord bot
          discordpy

          # Web scraping
          playwright

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
            pkgs.entr
            pkgs.sqlite
            pkgs.plantuml
            pkgs.visidata
            pkgs.playwright-driver.browsers
          ];

          shellHook = ''
            echo "ASAF dev shell ready (Python $(python --version))"
            [ -f .env ] && export $(grep -v '^#' .env | xargs) && echo ".env loaded"
            export PLAYWRIGHT_BROWSERS_PATH=${pkgs.playwright-driver.browsers}
            export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
          '';
        };
      });
}
