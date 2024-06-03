bindkey -v
stty -ixon
bindkey '^R' history-incremental-search-backward
bindkey '^S' history-incremental-search-forward
zstyle ':completion:*' menu select
LISTMAX=10000
# Fix invisible comments (black comment on black background)
ZSH_HIGHLIGHT_STYLES[comment]=fg=250
