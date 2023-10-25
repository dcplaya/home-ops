{ inputs, outputs, ... }: {
  imports = [
    ./locale.nix
    ./nix.nix
    ./openssh.nix
    ./systemd-initrd.nix
  ] ++ (builtins.attrValues {});

  nixpkgs = {
    # Add overlays here
    overlays = [
      outputs.overlays.unstable-packages
    ];
    # Configure your nixpkgs instance
    config = {
      # Disable if you don't want unfree packages
      allowUnfree = true;
    };
  };

  # Increase open file limit for sudoers
  security.pam.loginLimits = [
    {
      domain = "@wheel";
      item = "nofile";
      type = "soft";
      value = "524288";
    }
    {
      domain = "@wheel";
      item = "nofile";
      type = "hard";
      value = "1048576";
    }
  ];
}