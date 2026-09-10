#!/usr/bin/env bash
# ============================================================================
# Localized messages for the installer scripts.
#
#   English · Deutsch · 日本語 · 中文
#
# Usage:  source "$(dirname "$0")/msg.sh"
#         x3_missing_label          # label of a missing item
#         x3_lang_hint              # hint: which language the program starts in
#         x3_summary "<root>" "<scripts>"
#         x3_check_note
#         x3_yesno_labels           # e.g. "y/N"
#
# The language comes from the environment (LC_ALL / LC_MESSAGES / LANG) and can
# be forced with X3LANG=en|de|ja|zh.
# ============================================================================

x3_lang() {
    case "${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}" in
        de*|*de_DE*) echo de ;;
        ja*|*ja_JP*) echo ja ;;
        zh*|*zh_*)   echo zh ;;
        *)           echo en ;;
    esac
}

X3LANG="${X3LANG:-$(x3_lang)}"
export X3LANG

x3_missing_label() {
    case "$X3LANG" in
        de) printf 'FEHLT' ;;
        ja) printf '不足' ;;
        zh) printf '缺少' ;;
        *)  printf 'MISSING' ;;
    esac
}

x3_yesno_labels() {
    case "$X3LANG" in
        de) printf 'j/N' ;;
        *)  printf 'y/N' ;;
    esac
}

x3_ready_title() {
    case "$X3LANG" in
        de) printf 'Fertig' ;;
        ja) printf '完了' ;;
        zh) printf '完成' ;;
        *)  printf 'Ready' ;;
    esac
}

x3_check_note() {
    case "$X3LANG" in
        de) printf 'Hinweis: Dies war nur eine Prüfung - es wurde nichts installiert.' ;;
        ja) printf '注意: これは確認のみで、何もインストールされていません。' ;;
        zh) printf '注意：这只是检查运行，未安装任何内容。' ;;
        *)  printf 'Note: this was a check run only - nothing was installed.' ;;
    esac
}

x3_lang_hint() {
    case "$X3LANG" in
        de) printf 'Sprache: Die Oberfläche startet automatisch auf Deutsch; umstellen unter Einstellungen -> Design -> Sprache.' ;;
        ja) printf '言語: 画面は日本語で起動します。変更は「設定 -> デザイン -> 言語」。' ;;
        zh) printf '语言：界面以中文启动；可在「设置 -> 界面 -> 语言」中更改。' ;;
        *)  printf 'Language: the interface follows your system language; change it in Settings -> Design -> Language.' ;;
    esac
}

x3_summary() {
    local root="$1" scripts="$2"
    case "$X3LANG" in
        de) cat <<EOF
  Desktop-Programm starten:   $root/x3gui.sh
  Drucken (Kommandozeile):    python3 $root/x3print.py text 'Hallo'
  Drucker prüfen:             python3 $root/x3print.py status
  Installation entfernen:     $scripts/install.sh --uninstall

  Die Desktop-Verknüpfung liegt auf dem Schreibtisch; das Programm legt beim
  ersten Start selbst eine an (Buttons "Erstellen"/"Entfernen" unter
  Einstellungen -> Design).

  Sprache: Die Oberfläche startet automatisch in der Sprache Ihres Systems
  (Deutsch, English, 日本語, 中文) - umstellen unter Einstellungen -> Design
  -> Sprache.

  Mehr Details: INSTALL.md (Installation) und README.de.md (Benutzung)
EOF
            ;;
        ja) cat <<EOF
  デスクトップアプリの起動:   $root/x3gui.sh
  コマンドラインから印刷:     python3 $root/x3print.py text 'こんにちは'
  プリンターの確認:           python3 $root/x3print.py status
  アンインストール:           $scripts/install.sh --uninstall

  デスクトップのショートカットは初回起動時にプログラム自身も作成します
  （「設定 -> デザイン」の「作成」/「削除」ボタン）。

  言語: 画面はお使いのシステムの言語で自動的に起動します（日本語、
  English、ドイツ語、中国語）。変更は「設定 -> デザイン -> 言語」。

  詳細: INSTALL.md（インストール）と README.ja.md（使い方）
EOF
            ;;
        zh) cat <<EOF
  启动桌面程序:               $root/x3gui.sh
  命令行打印:                 python3 $root/x3print.py text '你好'
  检查打印机:                 python3 $root/x3print.py status
  卸载:                       $scripts/install.sh --uninstall

  桌面快捷方式在程序首次启动时也会自动创建（「设置 -> 界面」中的
  「创建」/「删除」按钮）。

  语言：界面会自动以系统语言启动（中文、English、德语、日语）。
  可随时在「设置 -> 界面 -> 语言」中更改。

  更多说明: INSTALL.md（安装）与 README.zh.md（使用）
EOF
            ;;
        *) cat <<EOF
  Start the desktop app:      $root/x3gui.sh
  Print from the command line: python3 $root/x3print.py text 'Hello'
  Check the printer:          python3 $root/x3print.py status
  Remove the installation:    $scripts/install.sh --uninstall

  The desktop shortcut is on your desktop; the app also creates one itself on
  its first start (buttons "Create"/"Remove" in Settings -> Design).

  Language: the interface starts in the language of your system (English,
  German, 日本語, 中文) - change it in Settings -> Design -> Language.

  More details: INSTALL.md (installation) and README.md (usage)
EOF
            ;;
    esac
}
