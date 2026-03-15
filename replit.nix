{ pkgs }: {
  deps = [
    pkgs.python312
    pkgs.nodejs_20
    pkgs.postgresql
    pkgs.gcc
    pkgs.libffi
    pkgs.openssl
  ];
}
