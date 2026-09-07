# ForkLab visual direction

The interface is a warehouse planning instrument: follow a process, change a policy,
then inspect the evidence. The original routing mark splits one input into two
orthogonal paths, ending at separate dispatch docks. The paths represent comparable
alternatives, not a promise that the candidate wins.

Palette: blueprint blue #163B59, workshop mist #EEF3F5, paper #FFFFFF,
dispatch orange #A84510, confirmed teal #126B63, exception red #B12A3B.
Blue and orange identify the two arms; teal and red describe measured verdicts.
Every verdict also has text and signed numbers.

Type: Barlow Semi Condensed for process labels and headings; Source Sans 3 for
controls and evidence, with system fallbacks. Numbers use tabular figures.

Layout, left aligned:

    brand + export
    study + workload facts
    dataset + baseline/candidate + run
    process and common time     | fork controls + assumptions
    paired evidence table
    searchable order trace

The dominant visual is a connected warehouse process. The initial empty state
explains the workflow without invented counts. Policy editing belongs next to the
process, rather than underneath an expanding order table. Numbered sections reflect
the actual read, compare, investigate sequence. Tables scroll locally on small
screens. Keyboard focus is explicit; no automatic animation is needed.

The mark, monochrome mark, reverse mark and process illustration are repository
native SVGs. They need no remote image provider and remain sharp at any scale.
