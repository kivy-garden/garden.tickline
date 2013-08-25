
# cython: profile=True
from bisect import bisect_left, bisect
from gui.android import logutils
from kivy.base import runTouchApp
from kivy.clock import Clock
from kivy.core.image import Image
from kivy.core.text import Label as CoreLabel
from kivy.effects.dampedscroll import DampedScrollEffect
from kivy.event import EventDispatcher
from kivy.graphics import InstructionGroup, Mesh
from kivy.graphics.context_instructions import Color
from kivy.graphics.vertex_instructions import Rectangle, Line
from kivy.lang import Builder
from kivy.metrics import dp, sp
from kivy.properties import ListProperty, NumericProperty, OptionProperty, \
    DictProperty, ObjectProperty, BoundedNumericProperty, BooleanProperty, \
    AliasProperty
from kivy.uix.accordion import Accordion, AccordionItem
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.stencilview import StencilView
from kivy.uix.widget import Widget
from kivy.vector import Vector
from kutils.funcs import MinimalStr
from kutils.math import about_eq
from math import ceil, floor
try:
    import cython
except ImportError:
    import kutils.dummy_cython as cython
if __debug__:
    from gui.android.profiler import profileit, lineprof
else:
    profileit = lineprof = lambda x: x
logger = logutils.getLogger(__name__, 'INFO', 'INFO')

class TickLabeller(Widget):
    '''handles labelling for a :class:`Tickline`. The TickLabeller is intended
    for a one-time use for each redraw. At the beginning of a redraw, the
    :class:`Tickline` removes all labels in :attr:`TickLabeller.labels` and
    create a new instance of TickLabeller that collects tick information
    throughout the redraw, using :meth:`TickLabeller.register`. After drawing
    all ticks and registering all tick information, the 
    :meth:`TickLabeller.make_labels()` should be called to explicitly 
    construct the :attr:`TickLabeller.labels`. See :meth:`Tickline._redraw`,
    :meth:`Tickline.clear_labels`, and :meth:`Tick.display' for actual
    usage.
    
    A class can inherit from this or just ducktype to be used similarly
    with :class:`Tickline` and :class:`Tick`.'''
    
    def __init__(self, tickline, **kw):
        super(TickLabeller, self).__init__(**kw)
        self.tickline = tickline
        self.registrar = {}
        
    def re_init(self, *args):
        '''method for reinitializing and accept registrations from a new
        redraw. Override if necessary.'''
        self.registrar = {}
        
    def register(self, tick, tick_index, tick_info):  
        tick_sc = tick.scale(self.tickline.scale)
        tickline = self.tickline
        texture = tick.get_label_texture(tick_index)
        if texture and tick_sc > tick.min_label_space:
            if tickline.is_vertical():
                x, tick_pos = (tick_info[0],
                               tick_info[1] + tick_info[3] / 2)
                align = tick.halign
                if align in ('left', 'line_right'):
                    l_x = x + tick.tick_size[1] + tickline.tick_label_padding
                else:
                    l_x = x - texture.width - tickline.tick_label_padding
                pos = (l_x, tick_pos - texture.height / 2)
            else:
                tick_pos, y = (tick_info[0] + tick_info[3] / 2,
                               tick_info[1])
                align = tick.valign
                if align in ('top', 'line_bottom'):
                    l_y = y - texture.height - tickline.tick_label_padding
                else:
                    l_y = y + tick.tick_size[1] + tickline.tick_label_padding
                pos = (tick_pos - texture.width / 2, l_y)
            if (tick_pos, align) not in self.registrar or \
                self.registrar[(tick_pos, align)][1] > tick.scale_factor:
                self.registrar[(tick_pos, align)] = (texture, pos, 
                                                     tick.scale_factor)
    def make_labels(self):
        canvas = self.tickline.tickruler.canvas
        group_id = self.group_id
        canvas.remove_group(group_id)
        with canvas:
            for texture, pos, _ in self.registrar.values():
                Rectangle(texture=texture, pos=pos,
                          size=texture.size,
                          group=group_id)
    @property
    def group_id(self):
        return self.__class__.__name__
    
class CompositeLabeller(TickLabeller):
    def __init__(self, tickline, labellers):
        self.tickline = tickline
        self.init_designater(labellers)
        
    def re_init(self, *args):
        for labeller in self.labellers:
            labeller.re_init(*args)
            
    def init_designater(self, labellers):
        self.designater = {}
        self.labellers = []
        for labeller_cls, tick_types in labellers.items():
            labeller = labeller_cls(self.tickline)
            self.labellers.append(labeller)
            for tp in tick_types:
                self.designater[tp] = labeller
                
    def register(self, tick, *args, **kw):
        labeller = self.designater[type(tick)]
        labeller.register(tick, *args, **kw)
        
    def make_labels(self):
        for labeller in self.labellers:
            labeller.make_labels()
        
Builder.load_string('''
#: import ScatterPlane kivy.uix.scatter.ScatterPlane
#: import KScatterPlane gui.android.kscatter.KScatterPlane
<Tickline>:
#     in_motion: 
#         (self.scroll_effect.velocity > 0 or self.scroll_effect.is_manual) \
#         if self.scroll_effect is not None else True
#         
    tickruler: tickruler
    Widget:
        id: tickruler
#     Label:
#         size: root.size
#         pos: root.pos
#         size_hint: (None, None) 
#         text: '{}, {}'.format(root.pos0, root.max_pos)
#         halighn: 'left'
#         text_size: self.width, None
#         font_size: self.height /10                   
''')
class Tickline(StencilView):
    def _get_scale_min(self, zoom, ticks):
        return min(tick.min_space / tick.scale(zoom) for tick in ticks)
    def _get_scale_max(self, zoom, ticks, max_pos):
        return max(max_pos / tick.scale(zoom) for tick in ticks)        
    #===========================================================================
    # public attributes
    #===========================================================================
    ticks = ListProperty()    
    '''a list of :class:`Tick` objects to draw'''
    zoomable = BooleanProperty(True)
    '''a toggle for whether this :class:`Tickline` can be zoomed in and out.'''
    draw_line = BooleanProperty(True)
    '''a toggle for whether the center line is drawn or not.'''
    background_color = ListProperty([0, 0, 0])
    '''color in RGB of the background.'''
    backward = BooleanProperty(False)
    '''By default, the Tickline runs left to right if :attr:`orientation`
    is 'horizontal' and bottom to top if it's 'vertical'. If :attr:`backward`
    is true, then this is reversed.'''
    def get_dir(self):
        return -1 if self.backward else 1
    def set_dir(self, val):
        self.backward = True if val <= 0 else False
    dir = AliasProperty(get_dir, set_dir, bind=['backward'])
    orientation = OptionProperty('vertical', options=['horizontal', 'vertical'])
    line_color = ListProperty([1, 1, 1, 1])
    line_width = NumericProperty(4.)
    line_offset = NumericProperty(0)
    '''how far the tickline should deviate from the center.'''
    def get_line_pos(self):
        if self.is_vertical():
            return self.center_x + self.line_offset
        else:
            return self.center_y + self.line_offset
    def set_line_pos(self, val):
        if self.is_vertical():
            self.line_offset = val - self.center_x
        else:
            self.line_offset = val - self.center_y
    line_pos = AliasProperty(get_line_pos, set_line_pos, bind=['orientation',
                                                               'line_offset',
                                                               'center_x',
                                                               'center_y'])
    
    min_index = NumericProperty(-float('inf'))
    '''The minimal value :attr:`global_index` can assume. Defaults to -inf,
    meaning the absence of such a minimum.'''
    max_index = NumericProperty(float('inf'))
    '''The maximal value :attr:`global_index` can assume. Defaults to inf,
    meaning the absence of such a maximum.'''
    index_0 = NumericProperty(0) 
    '''gives the index, likely fractional, that corresponds to 
    ``self.x`` if :attr:`orientation` is 'vertical', or ``self.y``
    if :attr:`orientation` is 'horizontal'. Note that this doesn't 
    depend on :attr:`Tickline.backward`.'''    
    
    index_1 = NumericProperty(10)
    '''gives the index, likely fractional, that corresponds to 
    ``self.right`` if :attr:`orientation` is 'vertical', or ``self.top``
    if :attr:`orientation` is 'horizontal'. Note that this doesn't 
    depend on :attr:`Tickline.backward`.'''    
 
    def get_index_mid(self):
        return (self.index_0 + self.index_1) / 2.
    def set_index_mid(self, val):
        half_length = self.max_pos / 2. / self.scale
        self.index_0 = val - half_length
        self.index_1 = val + half_length
    index_mid = AliasProperty(get_index_mid, set_index_mid,
                              bind=['index_0', 'index_1'])
    '''returns the index corresponding to the middle of the tickline.
    Setting this attribute as the effect of translating the tickline.
    '''
    
    def get_max_pos(self):
        return self.size[1 if self.is_vertical() else 0]  
    def set_max_pos(self, val):
        if self.is_vertical():
            self.size[1] = val
        else:
            self.size[0] = val
    max_pos = AliasProperty(get_max_pos, set_max_pos, cache=True,
                            bind=['size', 'orientation'])
    
    def get_pos0(self):
        return self.y if self.is_vertical() else self.x
    def set_pos0(self, val):
        if self.is_vertical():
            self.y = val
        else:
            self.x = val
    pos0 = AliasProperty(get_pos0, set_pos0,
                         bind=['size', 'x', 'y', 'orientation'])
    '''the coordinate of Tickline that's along the direction it extends.'''
    
    scale_min = NumericProperty(0)
    '''the parameter to be passed to the scatter tied to this Tickline, 
    specifying the max that one can zoom out. If None, then one can
    zoom out as long as the widest set of ticks has more than its 
    :attr:`~Tick.min_space`.'''
    scale_max = NumericProperty(float('inf'))
    '''the parameter to be passed to the scatter tied to this Tickline, 
    specifying the max that one can zoom in. If None, then one can zoom in
    as long as the narrowest set of ticks has spacing no greater than this
    Tickline's width (if it's horizontal) or height (if it's vertical).'''
    tick_label_padding = NumericProperty(0)
    '''the padding between a tick and its label.'''
    labeller_cls = ObjectProperty(TickLabeller)
    '''the class used to handle labelling.'''
    labeller = ObjectProperty(None)
    
#     scatterplane = ObjectProperty(None)
#     '''the :class:`KScatterPlane` instance passing touch signals to this
#     :class:`Tickline`.'''
    densest_tick = ObjectProperty(None)
    '''represents the smallest interval shown on screen.'''
    
    in_motion = BooleanProperty(False)
    translation_touches = BoundedNumericProperty(1, min=1)
    '''decides whether translation is triggered by a single touch 
    or multiple touches.'''
    drag_threshold = NumericProperty('20sp')
    '''the threshold to determine whether a touch constitutes a scroll.'''
    
#     texture = ObjectProperty(None)
    #===========================================================================
    # scatter attributes 
    #===========================================================================
    
    @cython.locals(sc=cython.double, zoom=cython.double)
    def get_scale(self):
        if self._versioned_scale is not None:
            scale = self._versioned_scale
            self._versioned_scale = None
            return scale
        try:
            return self.max_pos / (self.index_1 - self.index_0) * self.dir 
        except ZeroDivisionError:
            return float('inf')
    def set_scale(self, val):
        self.index_1 = self.index_0 + self.dir * self.max_pos / val
    scale = AliasProperty(get_scale, set_scale,
                          bind=['index_0', 'index_1', 'max_pos', 'dir'])
    scroll_effect_cls = ObjectProperty(DampedScrollEffect)
    scroll_effect = ObjectProperty(None, allownone=True)
    ''':attr:`scroll_effect`.value should always point toward :attr:`index_mid`.
    '''
    #===========================================================================
    # other private attributes
    #===========================================================================
    tickruler = ObjectProperty(None)
    scale_tolerances = ListProperty()
    '''essentially::
    
        sorted([(tick.scale_factor * tick.min_space, tick) for tick in self.ticks])
        
    This is used to determine :attr:`densest_tick`'''
    line_instr = ObjectProperty(None)
    '''instruction for drawing the *line*.'''
    line_color_instr = ObjectProperty(None)
    '''instruction for line color.'''
    _versioned_scale = NumericProperty(None, allownone=True)
    '''(internal) used to suppress :attr:`scale` change during a translation.'''
    
#     line_intercept = NumericProperty(0)
    
    #===========================================================================
    # methods 
    #===========================================================================
    def __init__(self, *args, **kw):
        super(Tickline, self).__init__(*args, **kw)
        self._touches = []
        self._last_touch_pos = {}
        self.scroll_effect = self.scroll_effect_cls()
        self._trigger_calibrate = \
                    Clock.create_trigger(self.calibrate_scroll_effect, -1)
        self.on_scroll_effect_cls()
        self.redraw = _redraw_trigger = \
                                Clock.create_trigger(self._redraw, -1)
        self.bind(index_0=_redraw_trigger,
                  index_1=_redraw_trigger,
                  pos=_redraw_trigger,
                  size=_redraw_trigger,
                  tickruler=_redraw_trigger,
                  orientation=_redraw_trigger,
                  ticks=_redraw_trigger)
        self.bind(index_mid=self._trigger_calibrate)
        self.init_center_line_instruction()
        self.init_background_instruction()
        self.on_ticks()
        self._update_densest_tick()
        self.labeller = self.labeller_cls(self)
#     def trigger_calibrate(self):
#         '''convenience method for triggering scroll effect calibration without
#         unnecessarily triggering translate effect.'''
#         self.scatterplane.no_translate_effect = True
#         self._trigger_calibrate()
#     def on_in_motion(self, *args):
#         if not self.scroll_effect:
#             return
#         print '{} now in motion:', self.scroll_effect.velocity, self.scroll_effect.is_manual
    def update_motion(self, *args):
        effect = self.scroll_effect
        self.in_motion = effect.velocity or effect.is_manual
    def on_backward(self, *args):
        if self.index_0 < self.index_1 and self.backward:
            self.index_0, self.index_1 = self.index_1, self.index_0
    def on_ticks(self, *args):
        self._update_tolerances()
        for tick in self.ticks:
            tick.bind(scale_factor=self._update_tolerances,
                      min_space=self._update_tolerances)
        if self.tickruler:
            canvas = self.tickruler.canvas
            canvas.clear()
            canvas.add(self.background_instr)
            if self.draw_line:
                canvas.add(self.line_color_instr)
                canvas.add(self.line_instr)
            for tick in self.ticks:
                canvas.add(tick.instr)
    def _update_tolerances(self, *args):
        self.scale_tolerances = sorted(
                               [(tick.scale_factor * tick.min_space, tick) 
                                for tick in self.ticks])
    def on_scroll_effect_cls(self, *args):
        effect = self.scroll_effect = self.scroll_effect_cls(round_value=False)
        self._update_effect_constants()
        self._trigger_calibrate()
        effect.bind(scroll=self._update_from_scroll)
        effect.bind(velocity=self.update_motion,
                    is_manual=self.update_motion)
#         effect.bind(scroll=self.print_scroll,
#                     velocity=self.print_scroll)
    def _update_effect_constants(self, *args):
        if not self.scroll_effect:
            return
#         print 'updating effect constants'
        scale = self.scale
        effect = self.scroll_effect
        effect.drag_threshold = self.drag_threshold / scale
        effect.min_distance = .1 / scale
        effect.min_velocity = .1 / scale
        effect.min_overscroll = .5 / scale
        return True
#         effect.spring_constant = 2.0 / scale
        
    def print_scroll(self, *args):
#         print 'self.scroll_effect.scroll changed to', self.scroll_effect.scroll
#         print '    velocity changed to', self.scroll_effect.velocity
        pass
    def translate_by(self, distance):
        self._versioned_scale = self.scale
        self.index_0 += distance
        self.index_1 += distance
    def _update_from_scroll(self, *args, **kw):
#         print '\tupdating indices from scroll'
        # possible dispatch loop here: will have to watch for it in the future
        new_mid = self.scroll_effect.scroll
        shift = new_mid - self.index_mid
        self.translate_by(shift)
    def on_pos(self, *args):
        self.redraw()
        self._trigger_calibrate()
    def on_max_index(self, *args):
        try:
            self._trigger_calibrate()
        except AttributeError:
            return
    def on_min_index(self, *args):
        try:
            self._trigger_calibrate()
        except AttributeError:
            return
    def calibrate_scroll_effect(self, *args, **kw):
#         print '\tcalibrating scroll effect'
        if not self.scroll_effect:
            return
        effect = self.scroll_effect
        effect.min = self.min_index 
        effect.max = self.max_index
        effect.value = self.index_mid
        return True
        
#     def calibrate_scroll_effect(self, *args, **kw):
#         '''calibrates the :attr:`~ScrollEffect.value`, 
#         :attr:`~ScrollEffect.min` and :attr:`~ScrollEffect.max` for 
#         :attr:`scroll_effect`. This is especially important for getting
#         overscroll effects.
#          
#         .. warning::
#             In most circumstances, do not use this method by itself; use
#             the trigger :attr:`_trigger_calibrate` instead. The reason
#             is that :attr:`scatterplane` transmits translation signals
#             by acruing a history of scroll values, and if multiple 
#             calls to this method happens before the 
#             :attr:`KScatterPlane.translate_effects_trigger` call, then
#             the translation will over compensate.'''
#          
#         logger.debug('scroll effect calibration starts: value %s',
#                      self.scroll_effect.value)
#         redraw = kw.pop('redraw', True)
#         min_ = self.scroll_effect.min = self.index2pos(0, i_mid=self.max_index)
#         max_ = self.scroll_effect.max = self.index2pos(0, i_mid=self.min_index)
#          
#         # there's a numeric accuracy bug with NumericProperty, such that
#         # if I directly set ``self.scroll_effect.value`` to ``v``
#         # it would trigger unnecessarily when they get too close and thus 
#         # initiate an infinite loop
#         v = self.index2pos(0)
# #         if about_eq(v, self.scroll_effect.value, tol=None):
# #             return
#         self.scatterplane.no_translate_effect = True
#         self.scroll_effect.value = v
#         if v > max_:
#             translate = Vector(0, (max_ - v) * self.dir) \
#                             if self.is_vertical() else \
#                                 Vector((max_ - v) * self.dir, 0)
#             self.update_from_translate(translate)
#             if redraw:
#                 self.redraw()
#         elif v < min_:
#             translate = Vector(0, (min_ - v) * self.dir) \
#                             if self.is_vertical() else \
#                                 Vector((min_ - v) * self.dir, 0)
#             self.update_from_translate(translate)
#             if redraw:
#                 self.redraw()
#         logger.debug('scroll effect calibration ends: value %s',
#                      self.scroll_effect.value)        
    def pos2index(self, pos, window=False):
        '''converts a position coordinate along the tickline direction to its
        index. If ``window`` is given as True, then the coordinate is assumed
        to be a window coordinate.'''
        return self.index_0 + self.dir * float(pos - window * self.pos0) / self.scale 
        
    def index2pos(self, index, i0=None, i1=None, i_mid=None):
        '''returns the position of a index (the global index, not a localized
        tick index), even if out of screen, based on the current :attr:`index_0`
        , :attr:`index_1`, and :attr:`max_pos`. Optionally, ``i0`` and/or
        ``i1`` can be given to replace respectively :attr:`index_0` and
        :attr:`index_1` in the calculation.
        
        .. note::
            the absolute position (relative to the window) is given.
            
        :param index: index to be converted to pos
        :param i0: the ``index_0`` corresponding to the situation in which
            we want to translate ``index`` to a position. By default we
            use :attr:`index_0`.
        :param i1: the ``index_1`` corresponding to the situation in which
            we want to translate ``index`` to a position. By default we
            use :attr:`index_1`.
        :param i_mid: The middle index, halfway between ``i0`` and ``i1``.
            If this is given, then ``i0`` and ``i1`` are calculated from 
            ``i_mid`` using the current :attr:`scale`.
         ''' 
        if i_mid is not None:
            i0 = i_mid - float(self.max_pos) / 2 / self.scale * self.dir
            i1 = i_mid + float(self.max_pos) / 2 / self.scale * self.dir
        else:
            i0, i1 = i0 or self.index_0, i1 or self.index_1
        return float(i0 - index) / (i0 - i1) * self.max_pos + self.pos0
        
    def calc_intercept(self, anchor, antianchor, to_window=False): 
        '''given 2 points ``anchor`` and ``antianchor`` (that usually
        represent 2 touches), 
        find the point on the Tickline that should be fixed through out
        a scatter operation.
        
        If ``to_window`` is given as True, the resulting coordinate is
        returned as a window coordinate. Otherwise, by default, the coordinate
        returned is with respect to this widget.
        
        .. note::
            ``anchor`` and ``antianchor`` are assumed to have window coordinates.
        '''
#         return to_window * self.pos0 + \
#             self._midpoint_intercept(anchor - self.pos, antianchor - self.pos)
        
        if self.is_vertical():
            return (anchor.y + antianchor.y) / 2 - (1 - to_window) * self.pos0
        else:
            return (anchor.x + antianchor.x) / 2 - (1 - to_window) * self.pos0
    
    def _midpoint_intercept(self, anchor, antianchor):       
        if self.is_vertical():
            return (anchor.y + antianchor.y) / 2
        else:
            return (anchor.x + antianchor.x) / 2
            
    def _extension_intercept(self, anchor, antianchor):
        '''find the x or y intercept where the line made by :attr:`self.anchor`
        and :attr:`self.antianchor` crosses our tickline.'''
        if self.is_vertical():
            x, y = anchor.x, anchor.y
            a, b = antianchor.x, antianchor.y
        else:
            y, x = anchor.x, anchor.y
            b, a = antianchor.x, antianchor.y
            
        w = self.line_pos
        return (a * y - w * y + w * b - x * b) / (a - x)
    
#     def update_tick_offset(self, inter, old_inter, scale, scale_factor,
#                            old_offset):
#         '''calculates the new :attr:`_tick_offset` based on old data and also
#         returns the change in :attr:`global_index`.
#         
#         :param inter: the destination of the fixed point of the scaling
#         :param old_inter: the source of the fixed point of the scaling
#         :param scale: the new :attr:`Tickline.scale`
#         :param scale_factor: the ratio of ``scale`` to the old scale
#         :param old_offset: the previous :attr:`Tickline.offset`'''
#         _t = r = (float(old_offset - old_inter) * 
#                   float(scale_factor)
#                   + inter)
#         r %= scale
#         global_change = int(round((r - _t) / scale)) * self.dir
#         assert r >= 0
#         return r, global_change
    def is_vertical(self):
        return self.orientation == 'vertical'
    def init_center_line_instruction(self):
        if not self.tickruler:
            return
        if not self.draw_line:
            self.line_color_instr = self.line_instr = None
            return
        self.line_color_instr = Color(*self.line_color)
        if self.is_vertical():
            self.line_instr = Line(points=[self.line_pos,
                                           self.y,
                                           self.line_pos,
                                           self.top],
                                   width=self.line_width,
                                   cap='none')
        else:
            self.line_instr = Line(points=[self.x,
                                           self.line_pos,
                                           self.right,
                                           self.line_pos],
                                   width=self.line_width,
                                   cap='none')
        _update_line_pts = self._update_line_pts
        self.bind(orientation=_update_line_pts,
                  pos=_update_line_pts,
                  size=_update_line_pts,
                  draw_line=_update_line_pts)
    def on_line_color(self, *args):
        self.line_color_instr.rgba = self.line_color
    def _update_line_pts(self, *args):
        if not self.line_instr:
            return
        if not self.draw_line:
            self.line_color_instr = self.line_instr = None
            return
        if self.is_vertical():
            self.line_instr.points = [self.line_pos,
                                      self.y,
                                      self.line_pos,
                                      self.top]
        else:
            self.line_instr.points = [self.x,
                                      self.line_pos,
                                      self.right,
                                      self.line_pos]
    def draw_center_line(self):
        if not self.tickruler:
            return
        with self.tickruler.canvas:
            Color(**self.line_color)
            if self.is_vertical():
                Line(points=[self.line_pos,
                             self.y,
                             self.line_pos,
                             self.top],
                     width=self.line_width,
                     cap='none')
            else:
                Line(points=[self.x,
                             self.line_pos,
                             self.right,
                             self.line_pos],
                     width=self.line_width,
                     cap='none')
#     def update_from_scatter_attrs(self):
#         if (self.anchor and self.antianchor and self.pantianchor):
#             assert not self.translate 
#             return self.update_from_zoom()
#         elif self.translate:
#             assert not (self.anchor and self.antianchor and self.pantianchor)
#             return self.update_from_translate()
#         else:
#             return False
#     def update_from_zoom(self, *args):
#         anchor, antianchor, pantianchor = \
#             self.anchor, self.antianchor, self.pantianchor
#         logger.debug('zoom: anchor %s, antianchor %s, pantianchor %s',
#                      anchor, antianchor, pantianchor)
#         try:
#             inter = self.calc_intercept(anchor, antianchor)
#             old_inter = self.calc_intercept(anchor, pantianchor)
#         except ZeroDivisionError:
#             return False
#         scale_factor = ((antianchor - anchor).length() / 
#                         (pantianchor - anchor).length())
#         logger.debug('scaling by %s', scale_factor)
#         self._tick_offset, global_change = \
#             self.update_tick_offset(inter, old_inter,
#                                     self.scale, scale_factor,
#                                     self._tick_offset)
#         self.global_index += global_change
#         assert self._tick_offset < self.scale
#         self.calibrate_scroll_effect()
#         return True
        
#     def update_from_translate(self, translate=None, *args):
#         translate = translate or self.translate
#         self._tick_offset += translate.y if self.is_vertical() \
#                                 else translate.x
#         _t = self._tick_offset
#         self._tick_offset %= self.scale
#         self.global_index += int(round((self._tick_offset - _t) / 
#                                        self.scale) * self.dir)
#         assert self._tick_offset < self.scale
#         self.calibrate_scroll_effect()
#         return True
        
    def clear_labels(self):
#         try:
#             for label in self.labeller.labels:
#                 self.remove_widget(label)
#         except AttributeError:
#             pass
        self.labeller.re_init()
    def init_background_instruction(self, *args):
        self.background_instr = instrg = InstructionGroup()
        instrg.add(Color(*self.background_color))
        instrg.add(Rectangle(pos=self.pos, size=self.size))
        update = self._update_background
        self.bind(background_color=update, 
                  pos=update,
                  size=update)
    def _update_background(self, *args):
        instrg = self.background_instr
        instrg.clear()
        instrg.add(Color(*self.background_color))
        instrg.add(Rectangle(pos=self.pos, size=self.size))
#     def draw_background(self, *args):
#         with self.tickruler.canvas:
#             Color(*self.background_color)
# #             if self.texture:
# #                 Rectangle(pos=self.pos,
# #                           size=self.size,
# #                           texture=self.texture)
# #             else:
#             Rectangle(pos=self.pos,
#                       size=self.size)  
#     @profileit              
    def _redraw(self, *args):
        if not self.tickruler:
            return
        self.clear_labels()
#         self.tickruler.canvas.clear()
#         self.draw_background()
#         if self.draw_line:
#             self.draw_center_line()
        # draw ticks
        for tick in self.ticks:
            tick.display(self)
        
#         self.tickruler.canvas.ask_update()
        # add labels
        
        self.labeller.make_labels()
#         for label in self.labeller.labels:
#             self.add_widget(label)
#     def redraw_with_scatter(self, *args):
#         if self.update_from_scatter_attrs():
#             self.redraw()
#     def on_size(self, *args):
#         for callback in self.before_resize:
#             callback()
#         self._redraw_trigger()
#     def on_tickruler(self, *args):
#         self._redraw_trigger()
#     def on_orientation(self, *args):
#         self._redraw_trigger()
#     def on_ticks(self, *args):
#         self._redraw_trigger()
    def _update_densest_tick(self, *args):
        tol = self.scale_tolerances
        scale = self.scale
        i = bisect(tol, scale)
        # tol[i-1] contains the tick with the largest scale_factor that can
        # still be displayed, or in other words, the tick with the smallest
        # interval
        try:
            self.densest_tick = tol[i - 1][1]
        except IndexError:
            self.densest_tick = None
    def on_scale(self, *args):
        logger.debug('Tickline %s updates scale to %s', self, self.scale)
        self._update_densest_tick()
        self._update_effect_constants()
        self.redraw()
       
    def on_touch_down(self, touch):
#         print '\t\tdispatching touch down'
        x, y = touch.x, touch.y
        
        if not self.collide_point(x, y):
            return False
        if super(Tickline, self).on_touch_down(touch):
            return True
        
        touch.grab(self)
        self._touches.append(touch)
        self._last_touch_pos[touch] = x, y
        if self.translate_now():
            self.scroll_effect.start(self.index_mid)
        else:
            self.scroll_effect.velocity = 0
            self.scroll_effect.cancel()
    def translate_now(self):
        return len(self._touches) == self.translation_touches
    def on_touch_move(self, touch):
#         print '\t\tdispatching touch move'
        x, y = touch.x, touch.y
        collide = self.collide_point(x, y)
        
        if collide and not touch.grab_current == self:
            if super(Tickline, self).on_touch_move(touch):
                return True
        
        if touch in self._touches and touch.grab_current == self:
            self.transform_with_touch(touch)
            self._last_touch_pos[touch] = x, y
            
        if collide:
            return True
    def transform_with_touch(self, touch): 
        changed = False
        scale = self.scale
        
        if self.translate_now():
            if not self.is_vertical():
                d = touch.x - self._last_touch_pos[touch][0]
            else:
                d = touch.y - self._last_touch_pos[touch][1]

            d = d / self.translation_touches
#             self.translate_by(- d / scale * self.dir)
            self.scroll_effect.update(self.index_mid - d / scale * self.dir)
            changed = True
            
        else:
            # no translation, so make sure cancel effects
            self.scroll_effect.velocity = 0
            self.scroll_effect.cancel()
            
        if len(self._touches) == 1 or not self.zoomable:
            return changed
        
        points = [Vector(self._last_touch_pos[t]) for t in self._touches]

        # we only want to transform if the touch is part of the two touches
        # furthest apart! So first we find anchor, the point to transform
        # around as the touch farthest away from touch
        anchor = max(points, key=lambda p: p.distance(touch.pos))

        # now we find the touch farthest away from anchor, if its not the
        # same as touch. Touch is not one of the two touches used to transform
        farthest = max(points, key=anchor.distance)
        if points.index(farthest) != self._touches.index(touch):
            return changed
        antianchor, pantianchor = Vector(*touch.pos), Vector(*touch.ppos)
        
        # the midpoint between the touches is to have the same index, while
        # all other points on the tickline are to scale away from this point.
        try:
            # note: these intercepts are local coordinates
            inter = self.calc_intercept(anchor, antianchor)
            old_inter = self.calc_intercept(anchor, pantianchor)
        except ZeroDivisionError:
            return False
        inter_index = self.pos2index(old_inter)
        scale_factor = ((antianchor - anchor).length() / 
                        (pantianchor - anchor).length())
        new_scale = scale_factor * scale
#         if new_scale < self.scale_min:
#             new_scale = self.scale_min
#         elif new_scale > self.scale_max:
#             new_scale = self.scale_max

        changed = inter != old_inter or new_scale != scale

        self.index_0 = index_0 = inter_index - self.dir * inter / new_scale
        self.index_1 = index_0 + self.dir * self.max_pos / new_scale 
        # need to update the scroll effect history so that on touch up
        # it doesn't jump
        self.scroll_effect.update(self.index_mid)
        self.scroll_effect.is_manual = True
        changed = True
        return changed
    
    def on_touch_up(self, touch):
#         print '\t\tdispatching touch up'
        x, y = touch.x, touch.y
        
        if not touch.grab_current == self:
            if super(Tickline, self).on_touch_up(touch):
                return True
        
        if touch in self._touches and touch.grab_state:
            if self.translate_now():
                self.scroll_effect.stop(self.index_mid)
            touch.ungrab(self)
            del self._last_touch_pos[touch]
            self._touches.remove(touch)
            
        if self.collide_point(x, y):
            return True
class Tick(MinimalStr, Widget): 
    '''an object that holds information about a set of ticks to be drawn
    into :class:`Tickline`.'''
    tick_size = ListProperty([dp(2), dp(8)])
    '''the first number always denotes the width (the shorter length).'''    
    halign = OptionProperty('left', options=['left', 'right',
                                              'line_left', 'line_right'])    
    valign = OptionProperty('bottom', options=['top', 'bottom',
                                               'line_top', 'line_bottom'])
    tick_color = ListProperty([1, 1, 1, 1])
    '''color of ticks drawn'''
    min_space = NumericProperty('10sp')
    '''if the spacing between consecutive ticks fall below this attribute,
    then this Tick will not be displayed.'''
    
    min_label_space = NumericProperty('37sp')
    '''if the spacing between consecutive ticks fall below this attribute,
    then this Tick's label will not be displayed.'''
    
    scale_factor = BoundedNumericProperty(1, min=1)
    '''The spacing of a Tick is determined by 
    :attr:`Tick.scale_factor` / :attr:`Tickline.scale`.'''
    
    offset = NumericProperty(0)
    '''Tick index is normally an integer, and a Tick index of ``idx`` is
    equivalent to a :attr:`Tickline.global_index` of ``idx / `` 
    :attr:`Tick.scale_factor`. When the :attr:`offset` is nonzero, however,
    the Tick index will be ``idx + offset``, corresponding to a global index
    of  ``(idx + offset)/ `` :attr:`Tick.scale_factor`. 
    
    This is for example used to display day ticks in a timezone aware manner.
    '''
    
    _mesh = ObjectProperty(None)
    '''The Mesh instruction that is used to draw ticks.'''
    
    instr = ObjectProperty(None)
    '''The instruction group used to draw ticks in addition to any other
    customizations.'''
    
    def __init__ (self, *args, **kw):
        self._mesh = Mesh(mode='triangle_strip')
        self._color = Color(*self.tick_color)
        self.instr = instr = InstructionGroup()
        instr.add(self._color)
        instr.add(self._mesh)
        super(Tick, self).__init__(*args, **kw)
    def on_tick_color(self, *args):
        self._color.rgba = self.tick_color
    def scale(self, sc):
        '''returns the spacing between ticks, given the global scale of 
        a :class:`Tickline`.
        
        :param sc: the :attr:`~Tickline.scale` of the :class:`Tickline` 
            this Tick belongs to'''
        return float(sc) / float(self.scale_factor)
    
    def unscale(self, tick_sc):
        '''reverse of :meth:`scale`.
        
        :param tick_sc: the scale of this Tick to be scaled back to the global
            scale of the :class:`Tickline` this Tick belongs to.
        '''
        return float(tick_sc) * float(self.scale_factor)
    
    def localize(self, index):
        '''turn a global index of :class:`Tickline` to the index used by
        this Tick.
        
        :param index: a global index of the :class:`Tickline` this Tick
            belongs to.
        '''
        return index * self.scale_factor
    
#     def get_label(self, index, **kw):
#         '''
#         Return a Label for a tick given its ordinal position. Return None if
#         there shouldn't be a label at ``index``.
#         
#         :param index: the ordinal number of a tick from the 0th tick, 
#             which is the tick that would have :attr:`Tickline.global_index` 0
#             if it were the first visible tick.
#         :param kw: keyword args passed to Label
#         '''
#         kw['font_size'] = self.tick_size[1] * 2
#         return Label(text=str(index), **kw)
    
    def get_label_texture(self, index, **kw):
        '''
        Return a Label *texture* for a tick given its ordinal position. 
        Return None if there shouldn't be a label at ``index``. This method
        is optimized for quickly drawing labels on screen.
        
        :param index: the ordinal number of a tick from the 0th tick, 
            which is the tick that would have :attr:`Tickline.global_index` 0
            if it were the first visible tick.
        :param kw: keyword args passed to Label
        '''        
        kw['font_size'] = self.tick_size[1] * 2
        label = CoreLabel(text=str(index), **kw)
        label.refresh()
        return label.texture
    
    def extended_index_0(self, tickline):
        d_tick = tickline.densest_tick
        localize = d_tick.localize
        globalize = d_tick.globalize
        
        return globalize(localize(tickline.index_0) + tickline.backward)
    
    def extended_index_1(self, tickline):
        d_tick = tickline.densest_tick
        localize = d_tick.localize
        globalize = d_tick.globalize
        
        return globalize(localize(tickline.index_1) - tickline.backward)
    
    def _index_condition(self, tickline, extended=False):
        '''If ``extended``, 
        returns a boolean functional that returns True iff the input is a
        localized tick index within the range displayable on screen, or just
        one above or below.
        
        Otherwise, the returned functional returns True iff the input is 
        strictly on screen'''
        
        if extended:
            index_0 = self.extended_index_0(tickline)
            index_1 = self.extended_index_1(tickline)        
        else:
            index_0 = tickline.index_0
            index_1 = tickline.index_1        
        localize = self.localize
        index_0 = localize(index_0)
        index_1 = localize(index_1)
        if tickline.backward:
            return lambda idx: index_1 <= idx <= index_0
        else:
            return lambda idx: index_0 <= idx <= index_1        
        
    def tick_iter(self, tickline):
        '''generates tick information for graphing and labeling in an iterator.
        By default, calls :meth:`tick_pos_index_iter` to return a pair 
        consisting of the position on screen and the localized tick index
        of the tick to be drawn.
        
        ..note::
            In general, the iterator should generate all the tick information
            for ticks to be drawn on screen. This in most cases would also
            include ticks just out of screen, but needs to be drawn partially.
        '''
        return self.tick_pos_index_iter(tickline)
    def tick_pos_index_iter(self, tl):
        '''given the parent :class:`Tickline` of this Tick, return an iterator
        of the positions and (localized) indices that correspond to ticks
        that should be drawn.
        
        :param tl: :class:`Tickline` that this Tick belongs to.
        '''
        
        tick_index, tick_pos, tick_sc = \
            self._get_index_n_pos_n_scale(tl, True)
#         print 'index, pos, sc', tick_index, tick_pos, tick_sc
#         tick_index -= 1
#         tick_pos -= tick_sc
        if tick_sc < self.min_space:
            raise StopIteration
        condition = self._index_condition(tl, True)
        pos0 = tl.y if tl.is_vertical() else tl.x
        while condition(tick_index):
#             print '\tyielding', tick_pos + pos0, tick_index
            yield tick_pos + pos0, tick_index
            tick_pos += tick_sc
            tick_index += tl.dir    
        raise StopIteration
    def display(self, tickline):
        '''main method for displaying Ticks. This is called after every
        scatter transform. Uses :attr:`draw` to handle actual drawing.
        
        :param tickline: a :class:`Tickline` instance that is guaranteed
            to have updated its :attr:`~Tickline.global_index`,
            :attr:`~Tickline._tick_offset`, and :attr:`~Tickline.scale`
            before calling this method.
        '''
        mesh = self._mesh
        self._vertices = []
        for tick_info in self.tick_iter(tickline):
            self.draw(tickline, tick_info)
        indices = list(range(len(self._vertices) // 4))
        mesh.vertices = self._vertices  
        mesh.indices = indices
    def draw(self, tickline, tick_info):
        '''Given information about a tick, present in on screen. May be 
        overriden to provide customized graphical representations, for 
        example, to graph a set of points.
        Uses :attr:`Tickline.labeller` to handle labelling.
        
        :param tickline: a :class:`Tickline` instance that is guaranteed
            to have updated its :attr:`~Tickline.global_index`,
            :attr:`~Tickline._tick_offset`, and :attr:`~Tickline.scale`
            before calling this method.
        :param tick_info: an object holding information about the tick to be
            drawn. By default, it's a pair holding the position and the index
            of the tick. Should be overriden to customize graphics.
        '''        
        tick_pos, tick_index = tick_info
        tick_rect = self.draw_tick(tickline, tick_pos)
        tickline.labeller.register(self, tick_index, tick_rect)
        
#     def index2pos(self, index, current_offset,
#                   offset_index=None, scale=None, dir_=1):
#         '''converts a tick index to its position on screen. It has a regular
#         version and a overloaded version, in which the only parameter in
#         addition to ``index`` is a :class:`Tickline`.
#         
#         :param index: index to be converted
#         :param current_offset: the position of the first visible tick. OR
#             a :class:`Tickline`, in which case, all parameters below are
#             not needed.
#         :param offset_index: the global tick index of the first visible tick
#         :param scale: the distance between neighboring ticks
#         '''
#         if isinstance(current_offset, Tickline):
#             return self._index2pos(index, 0,
#                                   self.localize(current_offset.index_0),
#                                   self.scale(current_offset.scale),
#                                   current_offset.dir)
#         return self._index2pos(index, current_offset, offset_index, scale, dir_)
#     
# #     @cython.cfunc
#     @cython.returns(cython.double)
#     @cython.locals(index=cython.double, current_offset=cython.double,
#                    offset_index=cython.double, scale=cython.double,
#                    dir_=cython.int)
#     def _index2pos(self, index, current_offset, offset_index, scale, dir_):
#         return (index - offset_index) * scale * dir_ + current_offset
    def _get_index_n_pos_n_scale(self, tickline, extended=False):    
        ''' utility function for getting the first tick index and position
         at the bottom of the screen, along with the localized scale of the Tick.
         
         If ``extended``, gives index and position for a tick just below the 
         screen, as determined by :attr:`Tickline.densest_tick`.
         
         :param tickline: a :class:`Tickline` instance, usually the one this
             Tick draws on.
         :param extended: flag for giving tick information for just below
             the display area. Defaults to False
         '''
        tick_sc = self.scale(tickline.scale)
        if extended:
            index_0 = self.extended_index_0(tickline)
        else:
            index_0 = tickline.index_0 
        tick_index_0 = index_0 * self.scale_factor
        trunc = floor if tickline.backward else ceil
        tick_index = trunc(tick_index_0 - tickline.dir * self.offset) + \
                        tickline.dir * self.offset
        tick_pos = (self.globalize(tick_index) - tickline.index_0) * tickline.scale * tickline.dir
#         tick_pos = self.index2pos(tick_index,
#                                   tickline._tick_offset,
#                                   tickline.global_index * self.scale_factor,
#                                   tick_sc, tickline.dir)
        return tick_index, tick_pos, tick_sc
    
    def globalize(self, tick_index):
        '''convert a index of this Tick to the global index used in the
        :class:`Tickline` this Tick belongs to. Note that the returned value
        is a float, since most likely ``tick_index`` refers to a fractional 
        tick.
        :param tick_index: the index of this Tick to be converted
        '''
        return float(tick_index) / self.scale_factor
    
#     @cython.ccall
    @cython.returns(Rectangle)
    @cython.locals(tick_pos=cython.double, return_only=cython.bint,
                   x=cython.double, y=cython.double,
                   width=cython.double, height=cython.double,
                   tw=cython.double, th=cython.double)
#     @lineprof
    def draw_tick(self, tickline, tick_pos, return_only=False):
        tw, th = self.tick_size
        if tickline.is_vertical():
            halign = self.halign
            if halign == 'left':
                x = tickline.x
            elif halign == 'line_left':
                x = tickline.line_pos - th
            elif halign == 'line_right':
                x = tickline.line_pos
            else:
                x = tickline.right - th
            y = tick_pos - tw / 2
            height, width = tw, th
        else:
            valign = self.valign
            if valign == 'top':
                y = tickline.top - th
            elif valign == 'line_top':
                y = tickline.line_pos
            elif valign == 'line_bottom':
                y = tickline.line_pos - th
            else:
                y = tickline.y
            x = tick_pos - tw / 2
            width, height = tw, th
        if not return_only:
            self._vertices.extend([x, y, 0, 0,
                                   x, y + height, 0, 0,
                                   x + width, y + height, 0, 0,
                                   x + width, y, 0, 0,
                                   x, y, 0, 0,
                                   x, y + height, 0, 0])
#         return Rectangle(pos=[x, y], size=[width, height])
        return (x, y, width, height)
    
class LabellessTick(Tick):
    def get_label_texture(self, *args, **kw):
        return None
    
Builder.load_string('''
<DataListTick>:
    halign: 'line_right'
''')
class DataListTick(Tick):
    '''takes a sorted list of tick indices and displays ticks at those marks.'''
    data = ListProperty([])
    '''assumed to be sorted least to greatest'''
    min_label_space = NumericProperty(0)
#     def _get_data_index_of_first_tick(self, tickline):
# #         index = bisect_left([self.index2pos(index, first_tick_pos,
# #                                              tick_index, tick_sc,
# #                                              tickline.dir)
# #                               for index in self.data[::tickline.dir]], 0)
#         index = bisect_left([tickline.index2pos(self.globalize(index))
#                               for index in self.data[::tickline.dir]], 0)
#         if tickline.backward:
#             index = len(self.data) - index - 1
#         return index
    def tick_pos_index_iter(self, tl):
        index_0 = self.localize(self.extended_index_0(tl))
        index_1 = self.localize(self.extended_index_1(tl))
        tick_sc = self.scale(tl.scale)
#         ref_tick_index, ref_tick_pos, tick_sc = \
#             self._get_index_n_pos_n_scale(tl)        
        if tick_sc < self.min_space:
            raise StopIteration
            
        try:
            data_index = bisect_left(self.data, index_1 if tl.backward 
                                                            else index_0)
            tick_index = self.data[data_index]
            condition = self._index_condition(tl, True)
            while condition(tick_index):
#                 print (tl.index2pos(self.globalize(tick_index)),
#                        tick_index)
                yield (tl.index2pos(self.globalize(tick_index)),
                       tick_index)
                data_index += 1
                tick_index = self.data[data_index]
        except IndexError:
            raise StopIteration
        
#         max_pos = tickline.max_pos
#         tick_index, first_tick_pos, tick_sc = \
#             self._get_index_n_pos_n_scale(tickline)        
#         if tick_sc < self.min_space:
#             yield StopIteration
# 
#         index = self._get_data_index_of_first_tick(first_tick_pos,
#                                                    tick_index, tick_sc,
#                                                    tickline)
#         try:
#             tick_pos = self.index2pos(self.data[index], first_tick_pos,
#                                       tick_index, tick_sc, tickline.dir)
#             while tick_pos <= max_pos:
#                 yield tick_pos, self.data[index]
#                 index += tickline.dir
#                 tick_pos = self.index2pos(self.data[index], first_tick_pos,
#                                           tick_index, tick_sc,
#                                           tickline.dir)
#         except IndexError:
#             yield StopIteration
    
if __name__ == '__main__':
#     acc = KAccordion()
#     acc.add_widget(TimelineAccordionItem())
#     typical_item = AccordionItem()
#     acc.add_widget(typical_item)
#     acc.orientation = 'vertical'
#     acc.min_space = '80pt'
    acc = Accordion(orientation='vertical')
    item = AccordionItem(title='hello')
    item.add_widget(Tickline(ticks=[Tick(tick_size=[4, 20], offset=.5),
                                    Tick(scale_factor=5.),
                                    LabellessTick(tick_size=[1, 4],
                                         scale_factor=25.),
                                    DataListTick(data=[-0.3, 1, 1.5,
                                                       2, 4, 8, 16, 23],
                                                 scale_factor=5.,
                                                 halign='line_right')
                                    ],
                             orientation='vertical',
                             backward=False,
                             min_index=0,
                             max_index=10))
    acc.add_widget(item)
    b = BoxLayout(padding=[10, 10, 10, 10], orientation='vertical')
    b.add_widget(acc)
    b.add_widget(Button(text='hello'))
    runTouchApp(b)
