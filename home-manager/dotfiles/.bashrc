source .commonrc
PS1="\[\e[32m\][\[\e[m\]\[\e[31m\]\u\[\e[m\]\[\e[33m\]@\[\e[m\]\[\e[32m\]\h\[\e[m\]:\[\e[36m\]\w\[\e[m\]\[\e[32m\]]\[\e[m\]\\$\[\e[m\] "

set completion-ignore-case on
set show-all-if-ambiguous on
bind 'TAB:menu-complete'

