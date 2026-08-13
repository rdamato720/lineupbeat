"""The FAQ content for each page. Real questions, honest answers."""

PROJECTIONS = [
    ("How are these fantasy football projections calculated?",
     "Each player's full-season stat line is projected first, then converted "
     "to points using standard scoring rules. The stat line is shown under "
     "each total so the number can be checked rather than taken on trust. "
     "Ranks are within position, and they change with the scoring format."),
    ("What is the difference between PPR, half PPR and standard scoring?",
     "PPR awards a point per reception, half PPR awards half a point, and "
     "standard awards none. Everything else is the same. That single rule "
     "moves pass-catching backs and slot receivers a long way: a back with "
     "70 catches can be a top-ten option in PPR and outside the top twenty "
     "in standard."),
    ("Why does a rank change when I switch scoring format?",
     "Because the ranking is recalculated on that format's points, not "
     "relabelled. A receiving back rises in PPR and falls in standard, and "
     "the board reorders to match. If it did not, two of the three formats "
     "would be showing the wrong order."),
    ("How often are the projections updated?",
     "They are rebuilt whenever the underlying projection sheet is revised, "
     "which is typically every few days through the preseason and after "
     "significant news. The update date is shown at the top of the page."),
    ("Are these projections per game or for the whole season?",
     "Full season. A seventeen-game total, not a weekly average."),
]

DRAFT_VALUE = [
    ("What does draft value mean in fantasy football?",
     "It is the gap between where the market drafts a player at his "
     "position and where our projection ranks him at that position. If the "
     "market takes someone as RB18 and we project him as RB11, the gap is "
     "+7 and we think the price is favourable."),
    ("What is ADP in fantasy football?",
     "Average draft position: the average pick at which a player is "
     "selected across a sample of real drafts. It is the market's opinion "
     "of a player, and it moves daily."),
    ("Why compare positional ranks instead of overall ADP?",
     "Because overall ADP and a positional projection are not comparable "
     "quantities. A quarterback going at pick 107 might be the fourteenth "
     "quarterback off the board, and QB14 against QB7 is a comparison you "
     "can reason about. Pick 107 against QB7 is not."),
    ("Does Strong Value mean I should reach for a player?",
     "No. It means that at a comparable price we would prefer him. ADP "
     "changes every day, and a player who looks expensive today can be "
     "fairly priced next week if his price falls."),
    ("What is LineupBeat implied ADP?",
     "If we rank a player QB7, we look at where the market's QB7 is "
     "actually being drafted. That pick number is the implied price, and "
     "the difference from his real ADP is how many picks of discount or "
     "premium our ranking suggests."),
    ("Does this include injury history or coaching?",
     "Deliberately not. Draft value is our projection against the market "
     "price and nothing else, so the number stays checkable. Durability, "
     "coaching and strength of schedule each have their own page."),
]

COACHING = [
    ("Who calls the plays for each NFL team in 2026?",
     "The primary play caller is listed for all 32 offenses, and it is "
     "often not the offensive coordinator. Buffalo's coordinator is Pete "
     "Carmichael Jr. while Joe Brady calls the plays; Chicago's is Press "
     "Taylor while Ben Johnson calls them."),
    ("How much does coaching affect fantasy football projections?",
     "Modestly. Role, talent, touches and targets matter far more. A "
     "coaching change is a tiebreaker between players at comparable price "
     "and comparable projection, not a reason to reach."),
    ("Which teams have a new offensive play caller in 2026?",
     "Seventeen, counted as a different primary caller from whoever "
     "finished the 2025 season calling that offense. That is a different "
     "number from how many teams hired a coordinator, and it is the one "
     "that matters."),
    ("What does No New Coaching Edge mean?",
     "That there is no new 2026 coaching change giving an additional "
     "reason to move those players either way. It is not negative. "
     "Cincinnati, Kansas City, San Francisco and Minnesota all carry it "
     "and all contain excellent picks."),
    ("What does Selective Target mean?",
     "The coaching change looks favourable to specific positions rather "
     "than the whole offense. Detroit is a tight end signal, which is not a "
     "reason to move a Lions receiver."),
]

SOS = [
    ("What is strength of schedule in fantasy football?",
     "How difficult a team's remaining opponents are. Measured two ways "
     "here: by opponent win percentage, which says whether a schedule is "
     "hard to win, and by fantasy points allowed by position, which says "
     "whether it is hard to score against."),
    ("Which is more useful, opponent record or points allowed?",
     "Points allowed by position, for fantasy purposes. A team can have a "
     "strong defense and a losing record, which reads as an easy matchup "
     "by record while being a hard one to throw on. The two genuinely "
     "diverge."),
    ("Why do the fantasy playoff weeks matter most?",
     "Most leagues decide their title in weeks 15 to 17. A team with an "
     "easy September and a brutal December wins you nothing, so that is "
     "the window worth drafting around. The board reorders for it."),
    ("How does strength of schedule update during the season?",
     "Only unplayed games count, so the number shrinks as the season goes. "
     "Before week one everything is last season's data; after that the "
     "current season blends in by weeks played, and the mix is shown on "
     "the page."),
    ("Does points allowed mean what one player will score?",
     "No. It is every player at that position combined, per game. A "
     "backfield splitting carries two ways puts both backs into its "
     "opponents' number, so it measures how good a matchup a defense is "
     "rather than what any one player would score."),
]
