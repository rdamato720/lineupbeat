#!/usr/bin/env python3
"""Focused contracts for the shared-shell visual regressions."""

from __future__ import annotations

import inspect
import unittest

import build_comparison_tool
import build_decision_room
import build_pages
import seo


def luminance(value: str) -> float:
    channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
              for c in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    light, dark = sorted((luminance(foreground), luminance(background)),
                         reverse=True)
    return (light + .05) / (dark + .05)


class VisualRegressionTests(unittest.TestCase):
    def test_shared_header_markup_always_carries_base_shell_css(self):
        markup = seo.site_nav("decision", "nfl")
        self.assertIn('id="shared-shell-css"', markup)
        for rule in (".topbar{position:sticky", "background:rgba(8,9,11,.96)",
                     'font:600 1.15rem/1 var(--agate)',
                     ".global-footer{margin-top:3rem", "html{-webkit-text-size-adjust"):
            self.assertIn(rule, markup)

    def test_decision_rooms_do_not_override_shell_typography(self):
        self.assertNotIn("--agate:Arial", build_decision_room.CSS)
        self.assertNotIn("--text:Arial", build_decision_room.CSS)
        header = build_decision_room.sport_header("college", "decision")
        self.assertIn('aria-current="page">Decision Room</a>', header)
        self.assertIn('data-nav-group="college" data-current="true"', header)
        self.assertIn("font-family:var(--text)", build_decision_room.CSS)

    def test_homepage_uses_lineupbeat_accent_for_both_sports(self):
        self.assertNotIn("#6de0bd", build_decision_room.CSS.lower())
        self.assertIn(".hp-college", build_decision_room.CSS)
        self.assertIn("border-top-color:var(--lime)", build_decision_room.CSS)

    def test_reviewed_archive_card_palette_has_accessible_contrast(self):
        css = build_decision_room.WIRE_PAGE_CSS
        self.assertIn("#wire .tile{background:#101513", css)
        self.assertIn("#wire .wrep,#wire .wimp{color:#f0f3ee}", css)
        self.assertGreaterEqual(contrast("#f0f3ee", "#101513"), 7)
        self.assertGreaterEqual(contrast("#b9c1ba", "#101513"), 4.5)

    def test_decision_family_and_tools_hub_active_states(self):
        source = inspect.getsource(build_comparison_tool.html)
        self.assertIn('seo.site_nav("decision"', source)
        self.assertNotIn('seo.site_nav("data"', source)
        self.assertIn('aria-current="page">Fantasy Data</a>', build_pages.DATA_HEADER)
        self.assertNotIn('aria-current="page">Projections</a>', build_pages.DATA_HEADER)

    def test_about_actions_use_button_system_and_mobile_wrap(self):
        source = inspect.getsource(build_pages.about_page)
        self.assertIn(".replace('lb-about-btn lb-about-btn-primary', 'btn')", source)
        self.assertIn(".replace('lb-about-btn lb-about-btn-secondary', 'btn ghost')", source)
        self.assertIn("gap:.85rem", source)
        self.assertIn("flex:1 1 100%", source)

    def test_shell_has_horizontal_overflow_guards(self):
        self.assertIn("overflow-x:clip", seo.SHELL_CSS)
        self.assertIn("max-width:100%", seo.SHELL_CSS)
        self.assertIn("min-width:0", seo.SHELL_CSS)

    def test_shared_header_uses_accessible_grouped_dropdowns(self):
        markup = seo.site_nav("rankings", "nfl")
        self.assertEqual(markup.count('class="navgroup"'), 3)
        self.assertIn(".navmenu{position:absolute", markup)
        self.assertIn("group.addEventListener('toggle'", markup)
        self.assertIn("openGroup.querySelector('summary').focus()", markup)


if __name__ == "__main__":
    unittest.main()
