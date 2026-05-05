<?php
require dirname(__FILE__) . '/../hsbox_config.php';
require dirname(__FILE__) . '/../hsbox_sub.php';

$tab_title='freeBox';
$tab_key   = 'freebox';
?>
<HTML>
<?php hsbox_lmenu($tab_title, $tab_key, $hsb_cfg); ?>
<body>
<script>window.location.href = '/freebox/';</script>
<p>freeBox Loaderを読み込み中...</p>
</body>
</HTML>
