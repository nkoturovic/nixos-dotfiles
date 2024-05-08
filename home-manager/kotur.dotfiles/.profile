export PATH=~/.npm-global/bin:$PATH

if [ -d "$HOME/.local/bin" ] ; then
    PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$HOME/.cargo/bin" ] ; then
    PATH="$HOME/.cargo/bin:$PATH"
fi
export PATH=~/.npm-global/bin:$PATH


export PATH=$HOME/go/bin:$PATH
export PATH=$HOME/.pulumi/bin:$PATH
export PATH=$HOME/bin:$PATH
export PATH=${PATH}:/home/kotur/.ec-login
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

export PATH=/home/kotur/.nix-profile/bin:/home/kotur/personal/DataGrip-2023.1/bin:/home/kotur/.cargo/bin:/home/kotur/.pulumi/bin:/home/kotur/go/bin:/home/kotur/.npm-global/bin:/home/kotur/.local/bin:/home/kotur/.local/bin:/home/kotur/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/var/lib/snapd/snap/bin:/home/kotur/.vector/bin:$PATH
. "$HOME/.cargo/env"

. "$HOME/.nix-profile/etc/profile.d/hm-session-vars.sh"

if [[ "$XDG_SESSION_DESKTOP" == "Sway" ]] ; then
    export XDG_CURRENT_DESKTOP="sway"
fi

# TODO(nkoturovic): Should remove it after update to ollama v0.1.29
export HSA_OVERRIDE_GFX_VERSION=10.3.0
