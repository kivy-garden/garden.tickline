Tickline
========

The :class:`Tickline` widget is designed to display a stream of measured data.

For example, a ruler-like :class:`Tickline` can 
display a tick every 10 pixels. A timeline-like :class:`Tickline` can display
dates and hours. If you have some 2-dimensional data, you can also use 
:class:`Tickline` to graph it against ticks demarcating some interval.

What makes :class:`Tickline` amazing is the ability to zoom and pan, and 
automatically adjust the ticks displayed in response to changing scale. 
If my tickline has multiple ticks, the ticks that are too fine will not 
be displayed. If I zoom in too much, I can always scroll it to see other parts 
of the tickline.

Usage
-----

A :class:`Tickline` by itself doesn't display anything other than perhaps
a line along its direction. It needs to be given :class:`Tick`s to draw. The
attribute :attr:`Tickline.ticks` contains a list of :class:`Tick`s that will
be displayed. 

Here is a simple example that will display ticks with integer labels::

    Tickline(ticks=[Tick()])

The power of :class:`Tickline` really comes through with multiple :class:`Tick`s
though. The following is a :class:`Tickline` that displays ticks with intervals
of 1, 1/5, and 1/10::

    Tickline(ticks=[Tick(), Tick(scale_factor=5.), Tick=(scale_factor=10.)])
    
You may notice that the :class:`Tick`s all label themselves by their *own* 
numbers, instead of, for example, the 1/5 tick labelling by 1/5 and the 1/10
tick labelling by 1/10. The 1/5, 2/5, 3/5, ... or 1/10, 2/10, ... would be
the *global indices* of the ticks. By default, however, the ticks are labelled
by *local indices*. This can be changed by setting :attr:`Tick.label_global` 
to True. 

A setting of interest may be :attr:`Tick.offset`, which causes the :class:`Tick`
to be draw at local indices ``..., offset - 2, offset - 1, offset, 
offset + 1, offset + 2, ...``.

Whether the tick*line* is displayed can be toggled with 
:attr:`Tickline.draw_line`, and its position can be set via
:attr:`Tickline.line_offset` and :attr:`Tickline.line_pos`. Other attributes
like :attr:`Tickline.line_color`, :attr:`Tickline.line_width`, 
and :attr:`Tickline.background_color` do what their names suggest to do.

If the tick*line* is drawn, the settings :attr:`Tick.halign` and 
:attr:`Tick.valign` can be used to specify where the tick is rendered. In short,
if the line is vertical, then ``halign`` is used, and can be one of
'left', 'right', 'line_left', 'line_right'. If the line is horizontal, then
``valign`` is used, and can be one of 'top', 'bottom', 'line_top', 
and 'line_bottom'. For more details go to their documentations.

Other tick customizations include :attr:`Tick.tick_color`, 
:attr:`Tick.tick_size`, :attr:`Tick.min_space`, and :attr:`Tick.min_label_space`.
The former two warrants little explanation (except that ``tick_size`` is
always given as a list ``[width, height]`` where ``width < height``, no matter
what the orientation of the :class:`Tickline` is). ``min_space`` controls
when a set of ticks are to be drawn. Specifically, if the space between 2 
consecutive ticks fall below ``min_space``, then the ticks are not drawn.
``min_label_space`` works similarly for the corresponding labels.

The orientation of a :class:`Tickline` can be set to 'horizontal' or 'vertical'
(default to 'vertical') and its direction can be changed through 
:attr:`Tickline.backward`.

If you'd like not to label a set of ticks, for example, like the milimeter ticks
on a typical ruler, then use :class:`LabellessTick`. 

If you'd like to draw ticks for only some numbers, use :class:`DataListTick`.

To put it all together::

    Tickline(ticks=[Tick(tick_size=[4, 20], offset=.5),
                    Tick(scale_factor=5., label_global=True),
                    LabellessTick(tick_size=[1, 4], scale_factor=25.),
                    DataListTick(data=[-0.3, 1, 1.5, 2, 4, 8, 16, 23],
                                 scale_factor=5.,
                                 halign='line_right',
                                 valign='line_top')
                    ],
             orientation='horizontal',
             backward=True)
            
Here's a :class:`Tickline` with 4 ticks drawn; 
the first set with interval of 1, the second, 1/5, the third, 1/25, and
the final set has ticks at the local indices provided by the list (so in
terms of global indices, the ticks are drawn at -0.3/5, 1/5, 1.5/5, etc).
The first set of ticks are drawn at every half integer. The second set of
ticks are labelled with global indices. The :class:`Tickline` is horizontal,
and runs from right to left. The tick*line* is centered in the middle of the
widget, and the :class:`DataListTick` sit on top of the line.

In addition to the attributes introduced above, :attr:`Tickline.min_index`,
:attr:`Tickline.max_index`, :attr:`Tickline.min_scale`, and 
:attr:`Tickline.max_scale` can be used to limit the sections of the Tickline
that can be shown, and how much can be zoomed in or out.

Customizations
--------------

There are 4 recommend extension points with regard to :class:`Tick` and
:class:`Tickline` in order of least to most change:
:meth:`Tick.draw`, :meth:`Tick.tick_iter`, :meth:`Tick.display`, and 
:meth:`Tickline.redraw_`. These 4 methods form the gist of redrawing operation.
In simple terms, :meth:`Tickline.redraw_` calls :meth:`Tick.display` for each
tick in :attr:`Tickline.ticks`. ``display`` then calls :meth:`Tick.tick_iter`
to obtain an iterator of relevant information regarding the list of ticks
that should be shown, and hands off each individual info to :meth:`Tick.draw`
to compute the graphics. 

A possible extension is to draw triangle ticks instead
of rectangles. In this case, overriding :meth:`Tick.draw` is most appropriate.
Another extension can be drawing a timeline, in which case 
:meth:`Tick.tick_iter` may be overriden to produce information about  
datetimes instead of plain global indices for ease of logic handling.

:class:`Tick`'s underlying drawing utilizes Mesh to ensure smooth graphics,
but this may not be appropriate for certain visuals. To change this behavior,
overriding :meth:`Tick.display` can be appropriate. 

Finally, if major changes to how :class:`Tickline` works are necessary,
override :meth:`Tickline.redraw_`. However, for most cases, it is recommended
to customize a version of *tick labeller*, which can be a subclass or
a ducktype of :class:`TickLabeller`. This is an object that handles
labelling and/or custom graphics for the tickline. See the class documentation
of :class:`TickLabeller` for more details.

Hack it!
--------

The technology behind :class:`Tickline` is actually quite versatile, and
it's possible to use it to build seemingly unrelated things. For example,
a selection wheel like in iOS has been created by subclassing it.
