from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QComboBox, QFrame, QCheckBox, QGridLayout,
                             QStackedWidget, QSpinBox, QDoubleSpinBox,
                             QSlider, QPushButton, QScrollArea, QSizePolicy)
from PyQt5.QtCore import Qt, pyqtSignal

class OverlayConfigPanel(QFrame):
    img_config_updated = pyqtSignal(dict) 
    ui_updated = pyqtSignal() 
    panel_closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320) 
        self.setMinimumHeight(150)
        self.setMaximumHeight(1000)
        
        self.setVisible(False)
        self.setObjectName("MainPanel")
        self.setStyleSheet("""
            QFrame#MainPanel { background-color: #0f172a; border: 1px solid #334155; border-radius: 8px; }
            QWidget { background-color: transparent; color: #e2e8f0; }
            QLabel { border: none; background: transparent; font-size: 11px; font-weight: bold; color: #94a3b8; }
            QSlider::groove:horizontal { border: 1px solid #334155; height: 6px; background: #1e293b; border-radius: 3px; }
            QSlider::handle:horizontal { background: #38bdf8; border: none; width: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::add-page:horizontal { background: #1e293b; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #0ea5e9; border-radius: 3px; }
            QCheckBox { font-size: 11px; font-weight: bold; border: none; background: transparent; }
            QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid #475569; background: #0f172a; }
            QCheckBox::indicator:checked { background: #38bdf8; border-color: #0ea5e9; }
            QSpinBox, QDoubleSpinBox, QComboBox { background-color: #0f172a; border: 1px solid #334155; padding: 4px; border-radius: 4px; color: white; font-size: 11px; }
            QStackedWidget { border: none; background: transparent; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.img_cfg = {}
        self.obj_cfg = {}
        self.shd_cfg = {}
        self.rst_cfg = {}
        self.model_lock_th = 90
        self.model_weak_th = 70
        self._is_loading = False

        self.page_image = self._create_image_setup_page()
        self.page_model = self._create_model_setup_page()
        self.page_align = self._create_align_setup_page()
        self.page_obj_line, self.page_obj_line_ui = self._create_line_setup_page("━ OBJECT LINE SETTINGS", self.obj_cfg)
        self.page_shd_line, self.page_shd_line_ui = self._create_line_setup_page("━ SHADOW LINE SETTINGS", self.shd_cfg)
        self.page_result = self._create_result_setup_page()

        self.stack.addWidget(self.page_image)    
        self.stack.addWidget(self.page_model)    
        self.stack.addWidget(self.page_align)    
        self.stack.addWidget(self.page_obj_line) 
        self.stack.addWidget(self.page_shd_line) 
        self.stack.addWidget(self.page_result)
        
        self.reset_to_defaults() 

    def hideEvent(self, event):
        self.panel_closed.emit()
        super().hideEvent(event)

    def reset_to_defaults(self):
        self._is_loading = True
        
        self.img_cfg.clear()
        self.img_cfg.update({'exp_auto': 1, 'exp_val': 200000, 'gain_auto': 0, 'gain_val': 10, 'contrast': 0, 'brightness': 0, 'vflip': 0, 'hmirror': 0, 'quality': 50})
        
        self.obj_cfg.clear()
        self.obj_cfg.update({'grayscale': True, 'blur': 1, 'gamma': 1.0, 'contrast': 1.0, 'morph': True, 'invert': False, 'scan_dir': 0, 'peak_mode': 0, 'kernel': 11, 'cut_ratio': 0.55, 'max_dev': 10, 'show_prep': False, 'show_line': True})
        
        self.shd_cfg.clear()
        self.shd_cfg.update({'grayscale': True, 'blur': 1, 'gamma': 1.0, 'contrast': 1.0, 'morph': True, 'invert': False, 'scan_dir': 0, 'peak_mode': 0, 'kernel': 12, 'cut_ratio': 0.55, 'max_dev': 10, 'show_prep': False, 'show_line': True})
        
        self.rst_cfg.clear()
        self.rst_cfg.update({
            'std_weak': 4, 'std_fail': 12, 'sig_weak': 4, 'sig_fail': 7,
            'dist_min': 10.0, 'dist_max': 50.0, 'view_indicator': True,
            'view_res_box': True, 'view_spec_box': True, 'view_status_box': True,
            'spec_alpha': 120, 
            'view_loc_roi': True, 'view_loc_cross': True,
            'view_align_roi': True, 'view_obj_roi': True, 'view_obj_line': True,
            'view_shd_roi': True, 'view_shd_line': True, 'view_dist_line': True,
            'res_x': 480, 'res_y': 20, 'res_w': 140, 'res_h': 60,
            'spec_x': 10, 'spec_y': 205, 'spec_w': 620, 'spec_h': 265,
            'status_x': 120, 'status_y': 20, 'status_w': 400, 'status_h': 100
        })
        self.model_lock_th = 90
        self.model_weak_th = 70

        self.chk_auto_exp.setChecked(True); self.sl_exp.setValue(200000)
        self.chk_auto_gain.setChecked(False); self.sl_gain.setValue(10)
        self.sl_contrast.setValue(0); self.sl_brightness.setValue(0); self.sl_quality.setValue(50)
        self.chk_vflip.setChecked(False); self.chk_hmirror.setChecked(False)

        def update_line_ui(cfg, ui_dict):
            ui_dict['grayscale'].setChecked(cfg['grayscale'])
            ui_dict['blur'].setValue(cfg['blur'])
            ui_dict['gamma'].setValue(cfg['gamma'])
            ui_dict['contrast'].setValue(cfg['contrast'])
            ui_dict['morph'].setChecked(cfg['morph'])
            ui_dict['invert'].setChecked(cfg['invert'])
            ui_dict['scan_dir'].setCurrentIndex(cfg['scan_dir'])
            ui_dict['peak_mode'].setCurrentIndex(cfg['peak_mode'])
            ui_dict['kernel'].setCurrentIndex(cfg['kernel'])
            ui_dict['cut_ratio'].setValue(cfg['cut_ratio'])
            ui_dict['max_dev'].setValue(cfg['max_dev'])
            ui_dict['show_prep'].setChecked(cfg['show_prep'])
            ui_dict['show_line'].setChecked(cfg['show_line'])

        update_line_ui(self.obj_cfg, self.page_obj_line_ui)
        update_line_ui(self.shd_cfg, self.page_shd_line_ui)

        self.sp_std_weak.setValue(self.rst_cfg['std_weak'])
        self.sp_std_fail.setValue(self.rst_cfg['std_fail'])
        self.sp_sig_weak.setValue(self.rst_cfg['sig_weak'])
        self.sp_sig_fail.setValue(self.rst_cfg['sig_fail'])
        self.sp_dist_min.setValue(self.rst_cfg['dist_min'])
        self.sp_dist_max.setValue(self.rst_cfg['dist_max'])
        
        self.chk_view_indicator.setChecked(self.rst_cfg['view_indicator'])
        self.chk_view_res_box.setChecked(self.rst_cfg['view_res_box'])
        self.chk_view_spec_box.setChecked(self.rst_cfg['view_spec_box'])
        self.chk_view_status_box.setChecked(self.rst_cfg['view_status_box'])
        self.sl_spec_alpha.setValue(self.rst_cfg['spec_alpha']) 
        
        self.chk_view_loc_roi.setChecked(self.rst_cfg['view_loc_roi'])
        self.chk_view_loc_cross.setChecked(self.rst_cfg['view_loc_cross'])
        self.chk_view_align.setChecked(self.rst_cfg['view_align_roi'])
        self.chk_view_obj_roi.setChecked(self.rst_cfg['view_obj_roi'])
        self.chk_view_obj_line.setChecked(self.rst_cfg['view_obj_line'])
        self.chk_view_shd_roi.setChecked(self.rst_cfg['view_shd_roi'])
        self.chk_view_shd_line.setChecked(self.rst_cfg['view_shd_line'])
        self.chk_view_dist.setChecked(self.rst_cfg['view_dist_line'])
        
        self._is_loading = False
        self.ui_updated.emit()
        self.img_config_updated.emit(self.img_cfg)

    def _apply_image_cfg(self, _=None):
        if self._is_loading: return 
        self.img_cfg['exp_auto'] = 1 if self.chk_auto_exp.isChecked() else 0
        self.img_cfg['exp_val'] = self.sl_exp.value()
        self.img_cfg['gain_auto'] = 1 if self.chk_auto_gain.isChecked() else 0
        self.img_cfg['gain_val'] = self.sl_gain.value()
        self.img_cfg['contrast'] = self.sl_contrast.value()
        self.img_cfg['brightness'] = self.sl_brightness.value()
        self.img_cfg['vflip'] = 1 if self.chk_vflip.isChecked() else 0
        self.img_cfg['hmirror'] = 1 if self.chk_hmirror.isChecked() else 0
        self.img_cfg['quality'] = self.sl_quality.value()
        self.img_config_updated.emit(self.img_cfg)

    def load_settings(self, meta_data):
        self._is_loading = True 
        try:
            if 'image' in meta_data:
                self.img_cfg.update(meta_data['image'])
                self.chk_auto_exp.setChecked(self.img_cfg.get('exp_auto', 1) == 1)
                self.sl_exp.setValue(self.img_cfg.get('exp_val', 200000))
                self.chk_auto_gain.setChecked(self.img_cfg.get('gain_auto', 0) == 1)
                self.sl_gain.setValue(self.img_cfg.get('gain_val', 10))
                self.sl_contrast.setValue(self.img_cfg.get('contrast', 0))
                self.sl_brightness.setValue(self.img_cfg.get('brightness', 0))
                self.chk_vflip.setChecked(self.img_cfg.get('vflip', 0) == 1)
                self.chk_hmirror.setChecked(self.img_cfg.get('hmirror', 0) == 1)
                self.sl_quality.setValue(self.img_cfg.get('quality', 50))
            def update_line_ui(cfg, ui_dict):
                ui_dict['grayscale'].setChecked(cfg.get('grayscale', True))
                ui_dict['blur'].setValue(cfg.get('blur', 1))
                ui_dict['gamma'].setValue(cfg.get('gamma', 1.0))
                ui_dict['contrast'].setValue(cfg.get('contrast', 1.0))
                ui_dict['morph'].setChecked(cfg.get('morph', True))
                ui_dict['invert'].setChecked(cfg.get('invert', False))
                ui_dict['scan_dir'].setCurrentIndex(cfg.get('scan_dir', 0))
                ui_dict['peak_mode'].setCurrentIndex(cfg.get('peak_mode', 0))
                ui_dict['kernel'].setCurrentIndex(cfg.get('kernel', 0))
                ui_dict['cut_ratio'].setValue(cfg.get('cut_ratio', 0.55))
                ui_dict['max_dev'].setValue(cfg.get('max_dev', 10))
                ui_dict['show_prep'].setChecked(cfg.get('show_prep', False))
                ui_dict['show_line'].setChecked(cfg.get('show_line', True))
            if 'obj_line' in meta_data:
                self.obj_cfg.update(meta_data['obj_line'])
                update_line_ui(self.obj_cfg, self.page_obj_line_ui)
            if 'shd_line' in meta_data:
                self.shd_cfg.update(meta_data['shd_line'])
                update_line_ui(self.shd_cfg, self.page_shd_line_ui)
        finally:
            self._is_loading = False
            self.img_config_updated.emit(self.img_cfg) 

    def load_rst_settings(self, rst_data):
        self._is_loading = True
        try:
            self.rst_cfg.update(rst_data)
            self.sp_std_weak.setValue(self.rst_cfg.get('std_weak', 4))
            self.sp_std_fail.setValue(self.rst_cfg.get('std_fail', 12))
            self.sp_sig_weak.setValue(self.rst_cfg.get('sig_weak', 4))
            self.sp_sig_fail.setValue(self.rst_cfg.get('sig_fail', 7))
            self.sp_dist_min.setValue(self.rst_cfg.get('dist_min', 10.0))
            self.sp_dist_max.setValue(self.rst_cfg.get('dist_max', 50.0))
            
            self.chk_view_indicator.setChecked(self.rst_cfg.get('view_indicator', False))
            self.chk_view_res_box.setChecked(self.rst_cfg.get('view_res_box', True))
            self.chk_view_spec_box.setChecked(self.rst_cfg.get('view_spec_box', True))
            self.chk_view_status_box.setChecked(self.rst_cfg.get('view_status_box', True))
            self.sl_spec_alpha.setValue(self.rst_cfg.get('spec_alpha', 120))
            
            self.chk_view_loc_roi.setChecked(self.rst_cfg.get('view_loc_roi', True))
            self.chk_view_loc_cross.setChecked(self.rst_cfg.get('view_loc_cross', True))
            self.chk_view_align.setChecked(self.rst_cfg.get('view_align_roi', True))
            self.chk_view_obj_roi.setChecked(self.rst_cfg.get('view_obj_roi', True))
            self.chk_view_obj_line.setChecked(self.rst_cfg.get('view_obj_line', True))
            self.chk_view_shd_roi.setChecked(self.rst_cfg.get('view_shd_roi', True))
            self.chk_view_shd_line.setChecked(self.rst_cfg.get('view_shd_line', True))
            self.chk_view_dist.setChecked(self.rst_cfg.get('view_dist_line', True))
        finally:
            self._is_loading = False
            self.ui_updated.emit()

    def update_box_coords(self, box_type, x, y, w, h):
        if box_type == "RESULT":
            self.rst_cfg['res_x'], self.rst_cfg['res_y'], self.rst_cfg['res_w'], self.rst_cfg['res_h'] = int(x), int(y), int(w), int(h)
        elif box_type == "SPEC":
            self.rst_cfg['spec_x'], self.rst_cfg['spec_y'], self.rst_cfg['spec_w'], self.rst_cfg['spec_h'] = int(x), int(y), int(w), int(h)
        elif box_type == "STATUS":
            self.rst_cfg['status_x'], self.rst_cfg['status_y'], self.rst_cfg['status_w'], self.rst_cfg['status_h'] = int(x), int(y), int(w), int(h)

    def show_page(self, page_name):
        pages = {"IMAGE": 0, "MODEL": 1, "ALIGN": 2, "OBJ_LINE": 3, "SHD_LINE": 4, "RESULT": 5}
        if page_name in pages: 
            self.stack.setCurrentIndex(pages[page_name])
            self.setVisible(True)
            self.adjustSize()

    def _create_close_btn(self):
        btn = QPushButton("CLOSE")
        btn.setStyleSheet("background-color: #475569; color: white; padding: 8px; border-radius: 4px; font-weight: bold; margin-top: 5px;")
        btn.clicked.connect(lambda: self.setVisible(False))
        return btn

    def _create_image_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("⚙️ VISION PRE-PROCESSING")
        title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        self.chk_auto_exp = QCheckBox("Auto Exposure"); self.chk_auto_exp.setChecked(self.img_cfg.get('exp_auto',1)==1)
        self.chk_auto_exp.toggled.connect(lambda c: [self.sl_exp.setEnabled(not c), self._apply_image_cfg()]); layout.addWidget(self.chk_auto_exp)
        self.sl_exp = self._create_slider(1000, 300000, self.img_cfg.get('exp_val',200000)); self.sl_exp.setEnabled(not self.chk_auto_exp.isChecked())
        self.sl_exp.valueChanged.connect(lambda v: self.lbl_exp_val.setText(f"{v:,} us")); self.sl_exp.sliderReleased.connect(self._apply_image_cfg)      
        self.lbl_exp_val = QLabel(f"{self.img_cfg.get('exp_val',200000):,} us"); self.lbl_exp_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Exposure Time", self.sl_exp, self.lbl_exp_val))
        self.chk_auto_gain = QCheckBox("Auto Gain"); self.chk_auto_gain.setChecked(self.img_cfg.get('gain_auto',0)==1)
        self.chk_auto_gain.toggled.connect(lambda c: [self.sl_gain.setEnabled(not c), self._apply_image_cfg()]); layout.addWidget(self.chk_auto_gain)
        self.sl_gain = self._create_slider(0, 32, self.img_cfg.get('gain_val',10)); self.sl_gain.setEnabled(not self.chk_auto_gain.isChecked())
        self.sl_gain.valueChanged.connect(lambda v: self.lbl_gain_val.setText(f"{v} dB")); self.sl_gain.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_gain_val = QLabel(f"{self.img_cfg.get('gain_val',10)} dB"); self.lbl_gain_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Gain Limit", self.sl_gain, self.lbl_gain_val))
        self.sl_contrast = self._create_slider(-3, 3, self.img_cfg.get('contrast',0))
        self.sl_contrast.valueChanged.connect(lambda v: self.lbl_contrast_val.setText(str(v))); self.sl_contrast.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_contrast_val = QLabel(str(self.img_cfg.get('contrast',0))); self.lbl_contrast_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Contrast", self.sl_contrast, self.lbl_contrast_val))
        self.sl_brightness = self._create_slider(-3, 3, self.img_cfg.get('brightness',0))
        self.sl_brightness.valueChanged.connect(lambda v: self.lbl_brightness_val.setText(str(v))); self.sl_brightness.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_brightness_val = QLabel(str(self.img_cfg.get('brightness',0))); self.lbl_brightness_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("Brightness", self.sl_brightness, self.lbl_brightness_val))
        self.sl_quality = self._create_slider(10, 100, self.img_cfg.get('quality',50))
        self.sl_quality.valueChanged.connect(lambda v: self.lbl_quality_val.setText(f"{v} %")); self.sl_quality.sliderReleased.connect(self._apply_image_cfg)
        self.lbl_quality_val = QLabel(f"{self.img_cfg.get('quality',50)} %"); self.lbl_quality_val.setAlignment(Qt.AlignRight)
        layout.addLayout(self._wrap_slider("JPEG Quality", self.sl_quality, self.lbl_quality_val))
        hbox_flip = QHBoxLayout()
        self.chk_vflip = QCheckBox("V-Flip"); self.chk_vflip.setChecked(self.img_cfg.get('vflip',0)==1); self.chk_vflip.toggled.connect(self._apply_image_cfg)
        self.chk_hmirror = QCheckBox("H-Mirror"); self.chk_hmirror.setChecked(self.img_cfg.get('hmirror',0)==1); self.chk_hmirror.toggled.connect(self._apply_image_cfg)
        hbox_flip.addWidget(self.chk_vflip); hbox_flip.addWidget(self.chk_hmirror); layout.addLayout(hbox_flip)
        layout.addSpacing(10) 
        layout.addStretch()
        layout.addWidget(self._create_close_btn())
        return page

    def _create_model_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("■ MODEL ROI SETTINGS"); title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        lbl_info = QLabel("설정할 항목이 없습니다.\n(화면에서 모델 영역을 직접 조작하세요.)")
        lbl_info.setAlignment(Qt.AlignCenter); lbl_info.setStyleSheet("color: #64748b; font-size: 12px; padding: 10px 0;")
        layout.addWidget(lbl_info)
        layout.addSpacing(10) 
        layout.addStretch()
        layout.addWidget(self._create_close_btn())
        return page

    def _create_align_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("⬚ ALIGNMENT SETTINGS"); title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        lbl_info = QLabel("설정할 항목이 없습니다.\n(좌표 및 크기는 화면에서 직접 조작)")
        lbl_info.setAlignment(Qt.AlignCenter); lbl_info.setStyleSheet("color: #64748b; font-size: 12px; padding: 10px 0;")
        layout.addWidget(lbl_info)
        layout.addSpacing(10) 
        layout.addStretch()
        layout.addWidget(self._create_close_btn())
        return page

    def _create_line_setup_page(self, title_text, cfg_dict):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel(title_text); title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)
        ui_dict = {}; grid = QGridLayout(); grid.setSpacing(10); row = 0

        chk_gray = QCheckBox("Enable Grayscale"); ui_dict['grayscale'] = chk_gray
        chk_gray.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'grayscale', v)); grid.addWidget(chk_gray, row, 0, 1, 2); row += 1
        
        grid.addWidget(QLabel("Gaussian Blur:"), row, 0)
        spin_blur = QSpinBox(); spin_blur.setRange(0, 5); ui_dict['blur'] = spin_blur
        spin_blur.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'blur', v)); grid.addWidget(spin_blur, row, 1); row += 1
        
        grid.addWidget(QLabel("Gamma Correction:"), row, 0)
        spin_gamma = QDoubleSpinBox()
        spin_gamma.setRange(0.1, 5.0)
        spin_gamma.setSingleStep(0.1)
        ui_dict['gamma'] = spin_gamma
        spin_gamma.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'gamma', v))
        grid.addWidget(spin_gamma, row, 1)
        row += 1

        grid.addWidget(QLabel("Contrast:"), row, 0)
        spin_contrast = QDoubleSpinBox()
        spin_contrast.setRange(0.1, 5.0)
        spin_contrast.setSingleStep(0.1)
        ui_dict['contrast'] = spin_contrast
        spin_contrast.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'contrast', v))
        grid.addWidget(spin_contrast, row, 1)
        row += 1

        chk_morph = QCheckBox("Enable Morphology"); ui_dict['morph'] = chk_morph
        chk_morph.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'morph', v)); grid.addWidget(chk_morph, row, 0, 1, 2); row += 1
        chk_invert = QCheckBox("Invert"); ui_dict['invert'] = chk_invert
        chk_invert.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'invert', v)); grid.addWidget(chk_invert, row, 0, 1, 2); row += 1

        line1 = QFrame(); line1.setFrameShape(QFrame.HLine); line1.setStyleSheet("color: #334155; margin: 5px 0;")
        grid.addWidget(line1, row, 0, 1, 2); row += 1

        grid.addWidget(QLabel("Scan Direction:"), row, 0)
        cmb_dir = QComboBox(); cmb_dir.addItems(["Top -> Bottom", "Bottom -> Top"]); ui_dict['scan_dir'] = cmb_dir
        cmb_dir.currentIndexChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'scan_dir', v)); grid.addWidget(cmb_dir, row, 1); row += 1
        
        grid.addWidget(QLabel("Peak Extraction:"), row, 0)
        cmb_peak = QComboBox(); cmb_peak.addItems(["Center", "Start (First Edge)", "End (Last Edge)"]); ui_dict['peak_mode'] = cmb_peak
        cmb_peak.currentIndexChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'peak_mode', v)); grid.addWidget(cmb_peak, row, 1); row += 1

        grid.addWidget(QLabel("Kernel Type:"), row, 0)
        cmb_kernel = QComboBox(); kernels = ["ORIGINAL", "OUTLINE_STD", "OUTLINE_NEG", "OUTLINE_STR", "OUTLINE_MAX", "EDGE_DET_4", "EDGE_DET_8", "HORZ_REINF", "VERT_REINF", "DIAG_1_LT_RB", "DIAG_2_RT_LB", "HORZ_D2B", "HORZ_B2D", "VERT_D2B", "VERT_B2D", "SHARPEN_L", "SHARPEN_H", "EMBOSS_STD", "EMBOSS_HORZ", "EMBOSS_HORZ_D2B", "EMBOSS_HORZ_B2D", "BLUR_HPF"]
        cmb_kernel.addItems(kernels); ui_dict['kernel'] = cmb_kernel
        cmb_kernel.currentIndexChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'kernel', v)); chk_morph.toggled.connect(cmb_kernel.setEnabled); grid.addWidget(cmb_kernel, row, 1); row += 1

        grid.addWidget(QLabel("Brightness Cut Ratio:"), row, 0)
        spin_cut = QDoubleSpinBox(); spin_cut.setRange(0.0, 1.0); spin_cut.setSingleStep(0.05); ui_dict['cut_ratio'] = spin_cut
        spin_cut.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'cut_ratio', v)); grid.addWidget(spin_cut, row, 1); row += 1
        grid.addWidget(QLabel("Max Deviation (px):"), row, 0)
        spin_dev = QSpinBox(); spin_dev.setRange(1, 50); ui_dict['max_dev'] = spin_dev
        spin_dev.valueChanged.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'max_dev', v)); grid.addWidget(spin_dev, row, 1); row += 1
        layout.addSpacing(10) 

        line2 = QFrame(); line2.setFrameShape(QFrame.HLine); line2.setStyleSheet("color: #334155; margin: 5px 0;")
        grid.addWidget(line2, row, 0, 1, 2); row += 1
        chk_show_prep = QCheckBox("Show Pre-processing"); ui_dict['show_prep'] = chk_show_prep
        chk_show_prep.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'show_prep', v)); grid.addWidget(chk_show_prep, row, 0, 1, 2); row += 1
        chk_show_line = QCheckBox("Show Line Result"); ui_dict['show_line'] = chk_show_line
        chk_show_line.toggled.connect(lambda v: self._update_dict_and_emit(cfg_dict, 'show_line', v)); grid.addWidget(chk_show_line, row, 0, 1, 2); row += 1
        layout.addLayout(grid)

        layout.addSpacing(10) 
        layout.addStretch()
        layout.addWidget(self._create_close_btn())
        return page, ui_dict

    def _create_result_setup_page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0,0,0,0)
        title = QLabel("📊 RESULT SETUP")
        title.setStyleSheet("color: #38bdf8; font-size: 14px; font-weight: 900; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-bottom: 5px;")
        layout.addWidget(title)

        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(8)

        def add_line():
            f = QFrame(); f.setFrameShape(QFrame.HLine); f.setStyleSheet("color: #334155; margin: 4px 0;"); layout.addWidget(f)

        grid1 = QGridLayout()
        grid1.addWidget(QLabel("STD Diff:"), 0, 0)
        self.sp_std_weak = QSpinBox(); self.sp_std_weak.setRange(0, 100); self.sp_std_weak.setPrefix("WEAK: ")
        self.sp_std_weak.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'std_weak', v)); grid1.addWidget(self.sp_std_weak, 0, 1)
        self.sp_std_fail = QSpinBox(); self.sp_std_fail.setRange(0, 100); self.sp_std_fail.setPrefix("FAIL: ")
        self.sp_std_fail.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'std_fail', v)); grid1.addWidget(self.sp_std_fail, 0, 2)
        
        grid1.addWidget(QLabel("SIG Diff:"), 1, 0)
        self.sp_sig_weak = QSpinBox(); self.sp_sig_weak.setRange(0, 100); self.sp_sig_weak.setPrefix("WEAK: ")
        self.sp_sig_weak.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'sig_weak', v)); grid1.addWidget(self.sp_sig_weak, 1, 1)
        self.sp_sig_fail = QSpinBox(); self.sp_sig_fail.setRange(0, 100); self.sp_sig_fail.setPrefix("FAIL: ")
        self.sp_sig_fail.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'sig_fail', v)); grid1.addWidget(self.sp_sig_fail, 1, 2)
        layout.addLayout(grid1)

        add_line()

        grid2 = QGridLayout()
        grid2.addWidget(QLabel("Distance Range:"), 0, 0)
        self.sp_dist_min = QDoubleSpinBox(); self.sp_dist_min.setRange(0, 500); self.sp_dist_min.setPrefix("Min: ")
        self.sp_dist_min.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'dist_min', v)); grid2.addWidget(self.sp_dist_min, 0, 1)
        self.sp_dist_max = QDoubleSpinBox(); self.sp_dist_max.setRange(0, 500); self.sp_dist_max.setPrefix("Max: ")
        self.sp_dist_max.valueChanged.connect(lambda v: self._update_dict_and_emit(self.rst_cfg, 'dist_max', v)); grid2.addWidget(self.sp_dist_max, 0, 2)
        layout.addLayout(grid2)

        add_line()
        
        lbl_visible = QLabel("👁️ UI VISIBILITY (TEST MODE)")
        lbl_visible.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold; margin-bottom: 2px;")
        layout.addWidget(lbl_visible)

        def make_chk(text, key):
            c = QCheckBox(text)
            c.toggled.connect(lambda v, k=key: self._update_dict_and_emit(self.rst_cfg, k, v))
            layout.addWidget(c); return c
        
        self.chk_view_indicator = make_chk("Indicator", 'view_indicator')
        self.chk_view_res_box = make_chk("Main Result", 'view_res_box')
        self.chk_view_spec_box = make_chk("Detail Specification", 'view_spec_box')
        
        hbox_alpha = QHBoxLayout()
        hbox_alpha.addWidget(QLabel("↳ Spec Box Opacity:"))
        self.sl_spec_alpha = self._create_slider(0, 255, self.rst_cfg.get('spec_alpha', 120))
        lbl_alpha_val = QLabel(str(self.rst_cfg.get('spec_alpha', 120)))
        self.sl_spec_alpha.valueChanged.connect(lambda v: [lbl_alpha_val.setText(str(v)), self._update_dict_and_emit(self.rst_cfg, 'spec_alpha', v)])
        hbox_alpha.addWidget(self.sl_spec_alpha); hbox_alpha.addWidget(lbl_alpha_val)
        layout.addLayout(hbox_alpha)
        
        self.chk_view_status_box = make_chk("Status/Progress", 'view_status_box')

        add_line()
        
        lbl_track = QLabel("🔍 TRACKING UI")
        lbl_track.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold; margin-bottom: 2px;")
        layout.addWidget(lbl_track)

        self.chk_view_loc_roi = make_chk("Location Region", 'view_loc_roi')
        self.chk_view_loc_cross = make_chk("Location Cross", 'view_loc_cross')
        self.chk_view_align = make_chk("Alignment Region", 'view_align_roi')
        self.chk_view_obj_roi = make_chk("Object Line Region", 'view_obj_roi')
        self.chk_view_obj_line = make_chk("Object Line", 'view_obj_line')
        self.chk_view_shd_roi = make_chk("Shadow Line Region", 'view_shd_roi')
        self.chk_view_shd_line = make_chk("Shadow Line", 'view_shd_line')
        self.chk_view_dist = make_chk("Distance Line", 'view_dist_line')

        layout.addSpacing(10) 
        layout.addStretch()
        layout.addWidget(self._create_close_btn())

        return page

    def _update_dict_and_emit(self, cfg_dict, key, val):
        if self._is_loading: return
        cfg_dict[key] = val; self.ui_updated.emit()
    def _create_slider(self, min_v, max_v, default_v):
        sl = QSlider(Qt.Horizontal); sl.setRange(min_v, max_v); sl.setValue(default_v); return sl
    def _wrap_slider(self, text, slider, val_label):
        vbox = QVBoxLayout(); hbox = QHBoxLayout(); hbox.addWidget(QLabel(text)); hbox.addStretch(); hbox.addWidget(val_label)
        vbox.addLayout(hbox); vbox.addWidget(slider); return vbox