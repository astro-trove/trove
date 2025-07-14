# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#   * Rearrange models' order
#   * Make sure each model has one field with primary_key=True
#   * Make sure each ForeignKey and OneToOneField has `on_delete` set to the desired behavior
#   * Remove `managed = False` lines if you wish to allow Django to create, modify, and delete the table
# Feel free to rename the models, but don't rename db_table values or field names.
from django.db import models


class AsassnQ3C(models.Model):
    aid = models.AutoField(primary_key=True)
    asassn_id = models.CharField(max_length=40, blank=True, null=True)
    source_id = models.CharField(max_length=20, blank=True, null=True)
    asassn_name = models.CharField(max_length=30, blank=True, null=True)
    other_names = models.CharField(max_length=32, blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    l = models.FloatField(blank=True, null=True)
    b = models.FloatField(blank=True, null=True)
    mean_vmag = models.FloatField(blank=True, null=True)
    amplitude = models.FloatField(blank=True, null=True)
    period = models.FloatField(blank=True, null=True)
    variable_type = models.CharField(max_length=10, blank=True, null=True)
    class_probability = models.FloatField(blank=True, null=True)
    lksl_statistic = models.FloatField(blank=True, null=True)
    rfr_score = models.FloatField(blank=True, null=True)
    epoch_hjd = models.FloatField(blank=True, null=True)
    gdr2_id = models.BigIntegerField(blank=True, null=True)
    phot_g_mean_mag = models.FloatField(blank=True, null=True)
    e_phot_g_mean_mag = models.FloatField(blank=True, null=True)
    phot_bp_mean_mag = models.FloatField(blank=True, null=True)
    e_phot_bp_mean_mag = models.FloatField(blank=True, null=True)
    phot_rp_mean_mag = models.FloatField(blank=True, null=True)
    e_phot_rp_mean_mag = models.FloatField(blank=True, null=True)
    bp_rp = models.FloatField(blank=True, null=True)
    parallax = models.FloatField(blank=True, null=True)
    parallax_error = models.FloatField(blank=True, null=True)
    parallax_over_error = models.FloatField(blank=True, null=True)
    pmra = models.FloatField(blank=True, null=True)
    pmra_error = models.FloatField(blank=True, null=True)
    pmdec = models.FloatField(blank=True, null=True)
    pmdec_error = models.FloatField(blank=True, null=True)
    vt = models.FloatField(blank=True, null=True)
    dist = models.FloatField(blank=True, null=True)
    allwise_id = models.CharField(max_length=20, blank=True, null=True)
    j_mag = models.FloatField(blank=True, null=True)
    e_j_mag = models.FloatField(blank=True, null=True)
    h_mag = models.FloatField(blank=True, null=True)
    e_h_mag = models.FloatField(blank=True, null=True)
    k_mag = models.FloatField(blank=True, null=True)
    e_k_mag = models.FloatField(blank=True, null=True)
    w1_mag = models.FloatField(blank=True, null=True)
    e_w1_mag = models.FloatField(blank=True, null=True)
    w2_mag = models.FloatField(blank=True, null=True)
    e_w2_mag = models.FloatField(blank=True, null=True)
    w3_mag = models.FloatField(blank=True, null=True)
    e_w3_mag = models.FloatField(blank=True, null=True)
    w4_mag = models.FloatField(blank=True, null=True)
    e_w4_mag = models.FloatField(blank=True, null=True)
    j_k = models.FloatField(blank=True, null=True)
    w1_w2 = models.FloatField(blank=True, null=True)
    w3_w4 = models.FloatField(blank=True, null=True)
    apass_dr9_id = models.BigIntegerField(blank=True, null=True)
    apass_vmag = models.FloatField(blank=True, null=True)
    e_apass_vmag = models.FloatField(blank=True, null=True)
    apass_bmag = models.FloatField(blank=True, null=True)
    e_apass_bmag = models.FloatField(blank=True, null=True)
    apass_gpmag = models.FloatField(blank=True, null=True)
    e_apass_gpmag = models.FloatField(blank=True, null=True)
    apass_rpmag = models.FloatField(blank=True, null=True)
    e_apass_rpmag = models.FloatField(blank=True, null=True)
    apass_ipmag = models.FloatField(blank=True, null=True)
    e_apass_ipmag = models.FloatField(blank=True, null=True)
    b_v = models.FloatField(blank=True, null=True)
    e_b_v = models.FloatField(blank=True, null=True)
    vector_x = models.FloatField(blank=True, null=True)
    vector_y = models.FloatField(blank=True, null=True)
    vector_z = models.FloatField(blank=True, null=True)
    reference = models.CharField(max_length=45, blank=True, null=True)
    periodic = models.BooleanField(blank=True, null=True)
    classified = models.BooleanField(blank=True, null=True)
    asassn_discovery = models.BooleanField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    edr3_source_id = models.CharField(max_length=25, blank=True, null=True)
    galex_id = models.CharField(max_length=25, blank=True, null=True)
    fuvmag = models.FloatField(blank=True, null=True)
    e_fuvmag = models.FloatField(blank=True, null=True)
    nuvmag = models.FloatField(blank=True, null=True)
    e_nuvmag = models.FloatField(blank=True, null=True)
    tic_id = models.CharField(max_length=16, blank=True, null=True)
    pm = models.FloatField(blank=True, null=True)
    ruwe = models.FloatField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'asassn_q3c'


class AuthGroup(models.Model):
    name = models.CharField(unique=True, max_length=150)

    class Meta:
        managed = False
        db_table = 'auth_group'


class AuthGroupPermissions(models.Model):
    group = models.ForeignKey(AuthGroup, models.DO_NOTHING)
    permission = models.ForeignKey('AuthPermission', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_group_permissions'
        unique_together = (('group', 'permission'),)


class AuthPermission(models.Model):
    name = models.CharField(max_length=255)
    content_type = models.ForeignKey('DjangoContentType', models.DO_NOTHING)
    codename = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'auth_permission'
        unique_together = (('content_type', 'codename'),)


class AuthUser(models.Model):
    password = models.CharField(max_length=128)
    last_login = models.DateTimeField(blank=True, null=True)
    is_superuser = models.BooleanField()
    username = models.CharField(unique=True, max_length=150)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.CharField(max_length=254)
    is_staff = models.BooleanField()
    is_active = models.BooleanField()
    date_joined = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'auth_user'


class AuthUserGroups(models.Model):
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)
    group = models.ForeignKey(AuthGroup, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_user_groups'
        unique_together = (('user', 'group'),)


class AuthUserUserPermissions(models.Model):
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)
    permission = models.ForeignKey(AuthPermission, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'auth_user_user_permissions'
        unique_together = (('user', 'permission'),)


class AuthtokenToken(models.Model):
    key = models.CharField(primary_key=True, max_length=40)
    created = models.DateTimeField()
    user = models.OneToOneField(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'authtoken_token'


class Candidates(models.Model):
    id = models.BigAutoField(primary_key=True)
    candidatenumber = models.IntegerField(blank=True, null=True)
    elongation = models.FloatField(blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    fwhm = models.FloatField(blank=True, null=True)
    snr = models.FloatField(blank=True, null=True)
    mag = models.FloatField(blank=True, null=True)
    magerr = models.FloatField(blank=True, null=True)
    classification = models.IntegerField(blank=True, null=True)
    cx = models.FloatField(blank=True, null=True)
    cy = models.FloatField(blank=True, null=True)
    cz = models.FloatField(blank=True, null=True)
    mlscore = models.FloatField(blank=True, null=True)
    targetid = models.ForeignKey('TomTargetsBasetarget', models.DO_NOTHING, db_column='targetid', blank=True, null=True)
    mlscore_bogus = models.FloatField(blank=True, null=True)
    mlscore_real = models.FloatField(blank=True, null=True)
    observation_record = models.ForeignKey('TomSurveysSurveyobservationrecord', models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'candidates'


class CustomCodeCredibleregioncontour(models.Model):
    id = models.BigAutoField(primary_key=True)
    probability = models.FloatField()
    pixels = models.JSONField()
    localization = models.ForeignKey('TomNonlocalizedeventsEventlocalization', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'custom_code_credibleregioncontour'
        unique_together = (('localization', 'probability'),)


class CustomCodeSurveyfieldcredibleregion(models.Model):
    id = models.BigAutoField(primary_key=True)
    smallest_percent = models.IntegerField()
    survey_field = models.ForeignKey('TomSurveysSurveyfield', models.DO_NOTHING)
    localization = models.ForeignKey('TomNonlocalizedeventsEventlocalization', models.DO_NOTHING)
    group = models.IntegerField(blank=True, null=True)
    rank_in_group = models.IntegerField(blank=True, null=True)
    probability_contained = models.FloatField(blank=True, null=True)
    scheduled_start = models.DateTimeField(blank=True, null=True)
    observation_record = models.ForeignKey('TomSurveysSurveyobservationrecord', models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'custom_code_surveyfieldcredibleregion'
        unique_together = (('localization', 'survey_field'),)


class CustomCodeTargetlistextra(models.Model):
    id = models.BigAutoField(primary_key=True)
    key = models.CharField(max_length=200)
    value = models.TextField()
    float_value = models.FloatField(blank=True, null=True)
    bool_value = models.BooleanField(blank=True, null=True)
    time_value = models.DateTimeField(blank=True, null=True)
    target_list = models.ForeignKey('TomTargetsTargetlist', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'custom_code_targetlistextra'
        unique_together = (('target_list', 'key'),)


class DesiSpecQ3C(models.Model):
    did = models.AutoField(primary_key=True)
    targetid = models.BigIntegerField(blank=True, null=True)
    survey = models.CharField(max_length=7, blank=True, null=True)
    program = models.CharField(max_length=6, blank=True, null=True)
    healpix = models.IntegerField(blank=True, null=True)
    spgrpval = models.IntegerField(blank=True, null=True)
    z = models.FloatField(blank=True, null=True)
    zerr = models.FloatField(blank=True, null=True)
    zwarn = models.BigIntegerField(blank=True, null=True)
    chi2 = models.FloatField(blank=True, null=True)
    coeff_0 = models.FloatField(blank=True, null=True)
    coeff_1 = models.FloatField(blank=True, null=True)
    coeff_2 = models.FloatField(blank=True, null=True)
    coeff_3 = models.FloatField(blank=True, null=True)
    coeff_4 = models.FloatField(blank=True, null=True)
    coeff_5 = models.FloatField(blank=True, null=True)
    coeff_6 = models.FloatField(blank=True, null=True)
    coeff_7 = models.FloatField(blank=True, null=True)
    coeff_8 = models.FloatField(blank=True, null=True)
    coeff_9 = models.FloatField(blank=True, null=True)
    npixels = models.BigIntegerField(blank=True, null=True)
    spectype = models.CharField(max_length=6, blank=True, null=True)
    subtype = models.CharField(max_length=20, blank=True, null=True)
    ncoeff = models.BigIntegerField(blank=True, null=True)
    deltachi2 = models.FloatField(blank=True, null=True)
    coadd_fiberstatus = models.IntegerField(blank=True, null=True)
    target_ra = models.FloatField(blank=True, null=True)
    target_dec = models.FloatField(blank=True, null=True)
    pmra = models.FloatField(blank=True, null=True)
    pmdec = models.FloatField(blank=True, null=True)
    ref_epoch = models.FloatField(blank=True, null=True)
    fa_target = models.BigIntegerField(blank=True, null=True)
    fa_type = models.IntegerField(blank=True, null=True)
    objtype = models.CharField(max_length=3, blank=True, null=True)
    subpriority = models.FloatField(blank=True, null=True)
    obsconditions = models.IntegerField(blank=True, null=True)
    release = models.IntegerField(blank=True, null=True)
    brickname = models.CharField(max_length=8, blank=True, null=True)
    brickid = models.IntegerField(blank=True, null=True)
    brick_objid = models.IntegerField(blank=True, null=True)
    morphtype = models.CharField(max_length=4, blank=True, null=True)
    ebv = models.FloatField(blank=True, null=True)
    flux_g = models.FloatField(blank=True, null=True)
    flux_r = models.FloatField(blank=True, null=True)
    flux_z = models.FloatField(blank=True, null=True)
    flux_w1 = models.FloatField(blank=True, null=True)
    flux_w2 = models.FloatField(blank=True, null=True)
    flux_ivar_g = models.FloatField(blank=True, null=True)
    flux_ivar_r = models.FloatField(blank=True, null=True)
    flux_ivar_z = models.FloatField(blank=True, null=True)
    flux_ivar_w1 = models.FloatField(blank=True, null=True)
    flux_ivar_w2 = models.FloatField(blank=True, null=True)
    fiberflux_g = models.FloatField(blank=True, null=True)
    fiberflux_r = models.FloatField(blank=True, null=True)
    fiberflux_z = models.FloatField(blank=True, null=True)
    fibertotflux_g = models.FloatField(blank=True, null=True)
    fibertotflux_r = models.FloatField(blank=True, null=True)
    fibertotflux_z = models.FloatField(blank=True, null=True)
    maskbits = models.IntegerField(blank=True, null=True)
    sersic = models.FloatField(blank=True, null=True)
    shape_r = models.FloatField(blank=True, null=True)
    shape_e1 = models.FloatField(blank=True, null=True)
    shape_e2 = models.FloatField(blank=True, null=True)
    ref_id = models.BigIntegerField(blank=True, null=True)
    ref_cat = models.CharField(max_length=2, blank=True, null=True)
    gaia_phot_g_mean_mag = models.FloatField(blank=True, null=True)
    gaia_phot_bp_mean_mag = models.FloatField(blank=True, null=True)
    gaia_phot_rp_mean_mag = models.FloatField(blank=True, null=True)
    parallax = models.FloatField(blank=True, null=True)
    photsys = models.CharField(max_length=1, blank=True, null=True)
    priority_init = models.BigIntegerField(blank=True, null=True)
    numobs_init = models.BigIntegerField(blank=True, null=True)
    cmx_target = models.BigIntegerField(blank=True, null=True)
    sv1_desi_target = models.BigIntegerField(blank=True, null=True)
    sv1_bgs_target = models.BigIntegerField(blank=True, null=True)
    sv1_mws_target = models.BigIntegerField(blank=True, null=True)
    sv1_scnd_target = models.BigIntegerField(blank=True, null=True)
    sv2_desi_target = models.BigIntegerField(blank=True, null=True)
    sv2_bgs_target = models.BigIntegerField(blank=True, null=True)
    sv2_mws_target = models.BigIntegerField(blank=True, null=True)
    sv2_scnd_target = models.BigIntegerField(blank=True, null=True)
    sv3_desi_target = models.BigIntegerField(blank=True, null=True)
    sv3_bgs_target = models.BigIntegerField(blank=True, null=True)
    sv3_mws_target = models.BigIntegerField(blank=True, null=True)
    sv3_scnd_target = models.BigIntegerField(blank=True, null=True)
    desi_target = models.BigIntegerField(blank=True, null=True)
    bgs_target = models.BigIntegerField(blank=True, null=True)
    mws_target = models.BigIntegerField(blank=True, null=True)
    scnd_target = models.BigIntegerField(blank=True, null=True)
    plate_ra = models.FloatField(blank=True, null=True)
    plate_dec = models.FloatField(blank=True, null=True)
    coadd_numexp = models.IntegerField(blank=True, null=True)
    coadd_exptime = models.FloatField(blank=True, null=True)
    coadd_numnight = models.IntegerField(blank=True, null=True)
    coadd_numtile = models.IntegerField(blank=True, null=True)
    mean_delta_x = models.FloatField(blank=True, null=True)
    rms_delta_x = models.FloatField(blank=True, null=True)
    mean_delta_y = models.FloatField(blank=True, null=True)
    rms_delta_y = models.FloatField(blank=True, null=True)
    mean_fiber_ra = models.FloatField(blank=True, null=True)
    std_fiber_ra = models.FloatField(blank=True, null=True)
    mean_fiber_dec = models.FloatField(blank=True, null=True)
    std_fiber_dec = models.FloatField(blank=True, null=True)
    mean_psf_to_fiber_specflux = models.FloatField(blank=True, null=True)
    tsnr2_gpbdark_b = models.FloatField(blank=True, null=True)
    tsnr2_elg_b = models.FloatField(blank=True, null=True)
    tsnr2_gpbbright_b = models.FloatField(blank=True, null=True)
    tsnr2_lya_b = models.FloatField(blank=True, null=True)
    tsnr2_bgs_b = models.FloatField(blank=True, null=True)
    tsnr2_gpbbackup_b = models.FloatField(blank=True, null=True)
    tsnr2_qso_b = models.FloatField(blank=True, null=True)
    tsnr2_lrg_b = models.FloatField(blank=True, null=True)
    tsnr2_gpbdark_r = models.FloatField(blank=True, null=True)
    tsnr2_elg_r = models.FloatField(blank=True, null=True)
    tsnr2_gpbbright_r = models.FloatField(blank=True, null=True)
    tsnr2_lya_r = models.FloatField(blank=True, null=True)
    tsnr2_bgs_r = models.FloatField(blank=True, null=True)
    tsnr2_gpbbackup_r = models.FloatField(blank=True, null=True)
    tsnr2_qso_r = models.FloatField(blank=True, null=True)
    tsnr2_lrg_r = models.FloatField(blank=True, null=True)
    tsnr2_gpbdark_z = models.FloatField(blank=True, null=True)
    tsnr2_elg_z = models.FloatField(blank=True, null=True)
    tsnr2_gpbbright_z = models.FloatField(blank=True, null=True)
    tsnr2_lya_z = models.FloatField(blank=True, null=True)
    tsnr2_bgs_z = models.FloatField(blank=True, null=True)
    tsnr2_gpbbackup_z = models.FloatField(blank=True, null=True)
    tsnr2_qso_z = models.FloatField(blank=True, null=True)
    tsnr2_lrg_z = models.FloatField(blank=True, null=True)
    tsnr2_gpbdark = models.FloatField(blank=True, null=True)
    tsnr2_elg = models.FloatField(blank=True, null=True)
    tsnr2_gpbbright = models.FloatField(blank=True, null=True)
    tsnr2_lya = models.FloatField(blank=True, null=True)
    tsnr2_bgs = models.FloatField(blank=True, null=True)
    tsnr2_gpbbackup = models.FloatField(blank=True, null=True)
    tsnr2_qso = models.FloatField(blank=True, null=True)
    tsnr2_lrg = models.FloatField(blank=True, null=True)
    sv_nspec = models.IntegerField(blank=True, null=True)
    sv_primary = models.BooleanField(blank=True, null=True)
    zcat_nspec = models.IntegerField(blank=True, null=True)
    zcat_primary = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'desi_spec_q3c'


class DjangoAdminLog(models.Model):
    action_time = models.DateTimeField()
    object_id = models.TextField(blank=True, null=True)
    object_repr = models.CharField(max_length=200)
    action_flag = models.SmallIntegerField()
    change_message = models.TextField()
    content_type = models.ForeignKey('DjangoContentType', models.DO_NOTHING, blank=True, null=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'django_admin_log'


class DjangoCommentFlags(models.Model):
    flag = models.CharField(max_length=30)
    flag_date = models.DateTimeField()
    comment = models.ForeignKey('DjangoComments', models.DO_NOTHING)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'django_comment_flags'
        unique_together = (('user', 'comment', 'flag'),)


class DjangoComments(models.Model):
    object_pk = models.CharField(max_length=64)
    user_name = models.CharField(max_length=50)
    user_email = models.CharField(max_length=254)
    user_url = models.CharField(max_length=200)
    comment = models.TextField()
    submit_date = models.DateTimeField()
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    is_public = models.BooleanField()
    is_removed = models.BooleanField()
    content_type = models.ForeignKey('DjangoContentType', models.DO_NOTHING)
    site = models.ForeignKey('DjangoSite', models.DO_NOTHING)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'django_comments'


class DjangoContentType(models.Model):
    app_label = models.CharField(max_length=100)
    model = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'django_content_type'
        unique_together = (('app_label', 'model'),)


class DjangoDramatiqTask(models.Model):
    id = models.UUIDField(primary_key=True)
    status = models.CharField(max_length=8)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    message_data = models.BinaryField()
    actor_name = models.CharField(max_length=300, blank=True, null=True)
    queue_name = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'django_dramatiq_task'


class DjangoMigrations(models.Model):
    app = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    applied = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'django_migrations'


class DjangoSession(models.Model):
    session_key = models.CharField(primary_key=True, max_length=40)
    session_data = models.TextField()
    expire_date = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'django_session'


class DjangoSite(models.Model):
    domain = models.CharField(unique=True, max_length=100)
    name = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'django_site'


class DjangoTasksDatabaseDbtaskresult(models.Model):
    id = models.UUIDField(primary_key=True)
    status = models.CharField(max_length=9)
    args_kwargs = models.JSONField()
    priority = models.IntegerField()
    task_path = models.TextField()
    queue_name = models.TextField()
    backend_name = models.TextField()
    run_after = models.DateTimeField(blank=True, null=True)
    enqueued_at = models.DateTimeField()
    finished_at = models.DateTimeField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    return_value = models.JSONField(blank=True, null=True)
    exception_class_path = models.TextField()
    traceback = models.TextField()

    class Meta:
        managed = False
        db_table = 'django_tasks_database_dbtaskresult'


class FermiLatQ3C(models.Model):
    fid = models.AutoField(primary_key=True)
    name = models.CharField(max_length=24)
    b = models.FloatField(blank=True, null=True)
    l = models.FloatField(blank=True, null=True)
    lbllac = models.FloatField(blank=True, null=True)
    pbllac = models.FloatField(blank=True, null=True)
    pfsrq = models.FloatField(blank=True, null=True)
    classification = models.CharField(max_length=16, blank=True, null=True)
    lbllaclit = models.FloatField(blank=True, null=True)
    classlit = models.CharField(max_length=16, blank=True, null=True)
    simbad = models.CharField(max_length=8, blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'fermi_lat_q3c'


class Gaiadr3VariableQ3C(models.Model):
    gid = models.AutoField(primary_key=True)
    ra = models.FloatField(blank=True, null=True)
    ra_error = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    dec_error = models.FloatField(blank=True, null=True)
    pmra = models.FloatField(blank=True, null=True)
    pmra_error = models.FloatField(blank=True, null=True)
    pmdec = models.FloatField(blank=True, null=True)
    pmdec_error = models.FloatField(blank=True, null=True)
    parallax = models.FloatField(blank=True, null=True)
    parallax_error = models.FloatField(blank=True, null=True)
    solution_id = models.BigIntegerField(blank=True, null=True)
    source_id = models.BigIntegerField(blank=True, null=True)
    classification = models.CharField(max_length=16, blank=True, null=True)
    best_class_name = models.CharField(max_length=26, blank=True, null=True)
    best_class_score = models.FloatField(blank=True, null=True)
    num_selected_g_fov = models.IntegerField(blank=True, null=True)
    mean_obs_time_g_fov = models.FloatField(blank=True, null=True)
    time_duration_g_fov = models.FloatField(blank=True, null=True)
    min_mag_g_fov = models.FloatField(blank=True, null=True)
    max_mag_g_fov = models.FloatField(blank=True, null=True)
    mean_mag_g_fov = models.FloatField(blank=True, null=True)
    median_mag_g_fov = models.FloatField(blank=True, null=True)
    range_mag_g_fov = models.FloatField(blank=True, null=True)
    trimmed_range_mag_g_fov = models.FloatField(blank=True, null=True)
    std_dev_mag_g_fov = models.FloatField(blank=True, null=True)
    skewness_mag_g_fov = models.FloatField(blank=True, null=True)
    kurtosis_mag_g_fov = models.FloatField(blank=True, null=True)
    mad_mag_g_fov = models.FloatField(blank=True, null=True)
    abbe_mag_g_fov = models.FloatField(blank=True, null=True)
    iqr_mag_g_fov = models.FloatField(blank=True, null=True)
    stetson_mag_g_fov = models.FloatField(blank=True, null=True)
    std_dev_over_rms_err_mag_g_fov = models.FloatField(blank=True, null=True)
    outlier_median_g_fov = models.FloatField(blank=True, null=True)
    num_selected_bp = models.IntegerField(blank=True, null=True)
    mean_obs_time_bp = models.FloatField(blank=True, null=True)
    time_duration_bp = models.FloatField(blank=True, null=True)
    min_mag_bp = models.FloatField(blank=True, null=True)
    max_mag_bp = models.FloatField(blank=True, null=True)
    mean_mag_bp = models.FloatField(blank=True, null=True)
    median_mag_bp = models.FloatField(blank=True, null=True)
    range_mag_bp = models.FloatField(blank=True, null=True)
    trimmed_range_mag_bp = models.FloatField(blank=True, null=True)
    std_dev_mag_bp = models.FloatField(blank=True, null=True)
    skewness_mag_bp = models.FloatField(blank=True, null=True)
    kurtosis_mag_bp = models.FloatField(blank=True, null=True)
    mad_mag_bp = models.FloatField(blank=True, null=True)
    abbe_mag_bp = models.FloatField(blank=True, null=True)
    iqr_mag_bp = models.FloatField(blank=True, null=True)
    stetson_mag_bp = models.FloatField(blank=True, null=True)
    std_dev_over_rms_err_mag_bp = models.FloatField(blank=True, null=True)
    outlier_median_bp = models.FloatField(blank=True, null=True)
    num_selected_rp = models.IntegerField(blank=True, null=True)
    mean_obs_time_rp = models.FloatField(blank=True, null=True)
    time_duration_rp = models.FloatField(blank=True, null=True)
    min_mag_rp = models.FloatField(blank=True, null=True)
    max_mag_rp = models.FloatField(blank=True, null=True)
    mean_mag_rp = models.FloatField(blank=True, null=True)
    median_mag_rp = models.FloatField(blank=True, null=True)
    range_mag_rp = models.FloatField(blank=True, null=True)
    trimmed_range_mag_rp = models.FloatField(blank=True, null=True)
    std_dev_mag_rp = models.FloatField(blank=True, null=True)
    skewness_mag_rp = models.FloatField(blank=True, null=True)
    kurtosis_mag_rp = models.FloatField(blank=True, null=True)
    mad_mag_rp = models.FloatField(blank=True, null=True)
    abbe_mag_rp = models.FloatField(blank=True, null=True)
    iqr_mag_rp = models.FloatField(blank=True, null=True)
    stetson_mag_rp = models.FloatField(blank=True, null=True)
    std_dev_over_rms_err_mag_rp = models.FloatField(blank=True, null=True)
    outlier_median_rp = models.FloatField(blank=True, null=True)
    in_vari_classification_result = models.BooleanField(blank=True, null=True)
    in_vari_rrlyrae = models.BooleanField(blank=True, null=True)
    in_vari_cepheid = models.BooleanField(blank=True, null=True)
    in_vari_planetary_transit = models.BooleanField(blank=True, null=True)
    in_vari_short_timescale = models.BooleanField(blank=True, null=True)
    in_vari_long_period_variable = models.BooleanField(blank=True, null=True)
    in_vari_eclipsing_binary = models.BooleanField(blank=True, null=True)
    in_vari_rotation_modulation = models.BooleanField(blank=True, null=True)
    in_vari_ms_oscillator = models.BooleanField(blank=True, null=True)
    in_vari_agn = models.BooleanField(blank=True, null=True)
    in_vari_microlensing = models.BooleanField(blank=True, null=True)
    in_vari_compact_companion = models.BooleanField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'gaiadr3variable_q3c'


class GladePlusQ3C(models.Model):
    gid = models.AutoField(primary_key=True)
    gn = models.IntegerField(blank=True, null=True)
    pgc = models.IntegerField(blank=True, null=True)
    gwgc = models.CharField(max_length=64, blank=True, null=True)
    hyperleda = models.CharField(max_length=64, blank=True, null=True)
    twomass = models.CharField(max_length=64, blank=True, null=True)
    wise = models.CharField(max_length=64, blank=True, null=True)
    sdss = models.CharField(max_length=64, blank=True, null=True)
    o_flag = models.CharField(max_length=1, blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    b = models.FloatField(blank=True, null=True)
    b_err = models.FloatField(blank=True, null=True)
    b_flag = models.IntegerField(blank=True, null=True)
    b_abs = models.FloatField(blank=True, null=True)
    j = models.FloatField(blank=True, null=True)
    j_err = models.FloatField(blank=True, null=True)
    h = models.FloatField(blank=True, null=True)
    h_err = models.FloatField(blank=True, null=True)
    k = models.FloatField(blank=True, null=True)
    k_err = models.FloatField(blank=True, null=True)
    w1 = models.FloatField(blank=True, null=True)
    w1_err = models.FloatField(blank=True, null=True)
    w2 = models.FloatField(blank=True, null=True)
    w2_err = models.FloatField(blank=True, null=True)
    w1_flag = models.IntegerField(blank=True, null=True)
    b_j = models.FloatField(blank=True, null=True)
    b_j_err = models.FloatField(blank=True, null=True)
    z_helio = models.FloatField(blank=True, null=True)
    z_cmb = models.FloatField(blank=True, null=True)
    z_flag = models.IntegerField(blank=True, null=True)
    v_err = models.FloatField(blank=True, null=True)
    z_err = models.FloatField(blank=True, null=True)
    d_l = models.FloatField(blank=True, null=True)
    d_l_err = models.FloatField(blank=True, null=True)
    dist_flag = models.IntegerField(blank=True, null=True)
    mstar = models.FloatField(blank=True, null=True)
    mstar_err = models.FloatField(blank=True, null=True)
    mrate = models.FloatField(blank=True, null=True)
    mrate_err = models.FloatField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'glade_plus_q3c'


class GuardianGroupobjectpermission(models.Model):
    object_pk = models.CharField(max_length=255)
    content_type = models.ForeignKey(DjangoContentType, models.DO_NOTHING)
    group = models.ForeignKey(AuthGroup, models.DO_NOTHING)
    permission = models.ForeignKey(AuthPermission, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'guardian_groupobjectpermission'
        unique_together = (('group', 'permission', 'object_pk'),)


class GuardianUserobjectpermission(models.Model):
    object_pk = models.CharField(max_length=255)
    content_type = models.ForeignKey(DjangoContentType, models.DO_NOTHING)
    permission = models.ForeignKey(AuthPermission, models.DO_NOTHING)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'guardian_userobjectpermission'
        unique_together = (('user', 'permission', 'object_pk'),)


class GwgcQ3C(models.Model):
    gid = models.AutoField(primary_key=True)
    pgc = models.IntegerField(blank=True, null=True)
    name = models.CharField(max_length=128)
    rah = models.FloatField(blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    tt = models.FloatField(blank=True, null=True)
    b_app = models.FloatField(blank=True, null=True)
    a = models.FloatField(blank=True, null=True)
    e_a = models.FloatField(blank=True, null=True)
    b = models.FloatField(blank=True, null=True)
    e_b = models.FloatField(blank=True, null=True)
    b_div_a = models.FloatField(blank=True, null=True)
    e_b_div_a = models.FloatField(blank=True, null=True)
    pa = models.FloatField(blank=True, null=True)
    b_abs = models.FloatField(blank=True, null=True)
    dist = models.FloatField(blank=True, null=True)
    e_dist = models.FloatField(blank=True, null=True)
    e_b_app = models.FloatField(blank=True, null=True)
    e_b_abs = models.FloatField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'gwgc_q3c'


class HecateQ3C(models.Model):
    hid = models.AutoField(primary_key=True)
    pgc = models.IntegerField(blank=True, null=True)
    objname = models.CharField(max_length=64, blank=True, null=True)
    id_ned = models.CharField(max_length=64, blank=True, null=True)
    id_nedd = models.CharField(max_length=64, blank=True, null=True)
    id_iras = models.CharField(max_length=64, blank=True, null=True)
    id_2mass = models.CharField(max_length=64, blank=True, null=True)
    sdss_photid = models.CharField(max_length=64, blank=True, null=True)
    sdss_specid = models.CharField(max_length=64, blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    f_astrom = models.IntegerField(blank=True, null=True)
    r1 = models.FloatField(blank=True, null=True)
    r2 = models.FloatField(blank=True, null=True)
    pa = models.FloatField(blank=True, null=True)
    rsource = models.CharField(max_length=1, blank=True, null=True)
    rflag = models.IntegerField(blank=True, null=True)
    t = models.FloatField(blank=True, null=True)
    e_t = models.FloatField(blank=True, null=True)
    incl = models.FloatField(blank=True, null=True)
    v = models.FloatField(blank=True, null=True)
    e_v = models.FloatField(blank=True, null=True)
    v_vir = models.FloatField(blank=True, null=True)
    e_v_vir = models.FloatField(blank=True, null=True)
    ndist = models.IntegerField(blank=True, null=True)
    edist = models.BooleanField(blank=True, null=True)
    d = models.FloatField(blank=True, null=True)
    e_d = models.FloatField(blank=True, null=True)
    d_lo68 = models.FloatField(blank=True, null=True)
    d_hi68 = models.FloatField(blank=True, null=True)
    d_lo95 = models.FloatField(blank=True, null=True)
    d_hi95 = models.FloatField(blank=True, null=True)
    dmethod = models.CharField(max_length=2, blank=True, null=True)
    ut = models.FloatField(blank=True, null=True)
    bt = models.FloatField(blank=True, null=True)
    vt = models.FloatField(blank=True, null=True)
    it = models.FloatField(blank=True, null=True)
    e_ut = models.FloatField(blank=True, null=True)
    e_bt = models.FloatField(blank=True, null=True)
    e_vt = models.FloatField(blank=True, null=True)
    e_it = models.FloatField(blank=True, null=True)
    ag = models.FloatField(blank=True, null=True)
    ai = models.FloatField(blank=True, null=True)
    s12 = models.FloatField(blank=True, null=True)
    s25 = models.FloatField(blank=True, null=True)
    s60 = models.FloatField(blank=True, null=True)
    s100 = models.FloatField(blank=True, null=True)
    q12 = models.FloatField(blank=True, null=True)
    q25 = models.FloatField(blank=True, null=True)
    q60 = models.FloatField(blank=True, null=True)
    q100 = models.FloatField(blank=True, null=True)
    wf1 = models.FloatField(blank=True, null=True)
    wf2 = models.FloatField(blank=True, null=True)
    wf3 = models.FloatField(blank=True, null=True)
    wf4 = models.FloatField(blank=True, null=True)
    e_wf1 = models.FloatField(blank=True, null=True)
    e_wf2 = models.FloatField(blank=True, null=True)
    e_wf3 = models.FloatField(blank=True, null=True)
    e_wf4 = models.FloatField(blank=True, null=True)
    wfpoint = models.BooleanField(blank=True, null=True)
    wftreat = models.BooleanField(blank=True, null=True)
    j = models.FloatField(blank=True, null=True)
    h = models.FloatField(blank=True, null=True)
    k = models.FloatField(blank=True, null=True)
    e_j = models.FloatField(blank=True, null=True)
    e_h = models.FloatField(blank=True, null=True)
    e_k = models.FloatField(blank=True, null=True)
    flag_2mass = models.IntegerField(blank=True, null=True)
    u = models.FloatField(blank=True, null=True)
    g = models.FloatField(blank=True, null=True)
    r = models.FloatField(blank=True, null=True)
    i = models.FloatField(blank=True, null=True)
    z = models.FloatField(blank=True, null=True)
    e_u = models.FloatField(blank=True, null=True)
    e_g = models.FloatField(blank=True, null=True)
    e_r = models.FloatField(blank=True, null=True)
    e_i = models.FloatField(blank=True, null=True)
    e_z = models.FloatField(blank=True, null=True)
    logl_tir = models.FloatField(blank=True, null=True)
    logl_fir = models.FloatField(blank=True, null=True)
    logl_60u = models.FloatField(blank=True, null=True)
    logl_12u = models.FloatField(blank=True, null=True)
    logl_22u = models.FloatField(blank=True, null=True)
    logl_k = models.FloatField(blank=True, null=True)
    ml_ratio = models.FloatField(blank=True, null=True)
    logsfr_tir = models.FloatField(blank=True, null=True)
    logsfr_fir = models.FloatField(blank=True, null=True)
    logsfr_60u = models.FloatField(blank=True, null=True)
    logsfr_12u = models.FloatField(blank=True, null=True)
    logsfr_22u = models.FloatField(blank=True, null=True)
    logsfr_hec = models.FloatField(blank=True, null=True)
    sfr_hec_flag = models.CharField(max_length=2, blank=True, null=True)
    logm_hec = models.FloatField(blank=True, null=True)
    logsfr_gsw = models.FloatField(blank=True, null=True)
    logm_gsw = models.FloatField(blank=True, null=True)
    min_snr = models.FloatField(blank=True, null=True)
    metal = models.FloatField(blank=True, null=True)
    flag_metal = models.IntegerField(blank=True, null=True)
    class_sp = models.IntegerField(blank=True, null=True)
    agn_s17 = models.CharField(max_length=1, blank=True, null=True)
    agn_hec = models.CharField(max_length=1, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'hecate_q3c'


class LsDr10Q3C(models.Model):
    lid = models.BigAutoField(primary_key=True)
    brickid = models.IntegerField(blank=True, null=True)
    objid = models.IntegerField(blank=True, null=True)
    z_phot_mean = models.FloatField(blank=True, null=True)
    z_phot_median = models.FloatField(blank=True, null=True)
    z_phot_std = models.FloatField(blank=True, null=True)
    z_phot_l68 = models.FloatField(blank=True, null=True)
    z_phot_u68 = models.FloatField(blank=True, null=True)
    z_phot_l95 = models.FloatField(blank=True, null=True)
    z_phot_u95 = models.FloatField(blank=True, null=True)
    z_phot_mean_i = models.FloatField(blank=True, null=True)
    z_phot_median_i = models.FloatField(blank=True, null=True)
    z_phot_std_i = models.FloatField(blank=True, null=True)
    z_phot_l68_i = models.FloatField(blank=True, null=True)
    z_phot_u68_i = models.FloatField(blank=True, null=True)
    z_phot_l95_i = models.FloatField(blank=True, null=True)
    z_phot_u95_i = models.FloatField(blank=True, null=True)
    z_spec = models.FloatField(blank=True, null=True)
    release = models.SmallIntegerField(blank=True, null=True)
    training = models.BooleanField(blank=True, null=True)
    training_i = models.BooleanField(blank=True, null=True)
    survey = models.TextField(blank=True, null=True)
    kfold = models.IntegerField(blank=True, null=True)
    kfold_i = models.IntegerField(blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    declination = models.FloatField(blank=True, null=True)
    flux_g = models.FloatField(blank=True, null=True)
    flux_r = models.FloatField(blank=True, null=True)
    flux_i = models.FloatField(blank=True, null=True)
    flux_z = models.FloatField(blank=True, null=True)
    flux_ivar_g = models.FloatField(blank=True, null=True)
    flux_ivar_r = models.FloatField(blank=True, null=True)
    flux_ivar_i = models.FloatField(blank=True, null=True)
    flux_ivar_z = models.FloatField(blank=True, null=True)
    flux_w1 = models.FloatField(blank=True, null=True)
    flux_w2 = models.FloatField(blank=True, null=True)
    flux_w3 = models.FloatField(blank=True, null=True)
    flux_w4 = models.FloatField(blank=True, null=True)
    flux_ivar_w1 = models.FloatField(blank=True, null=True)
    flux_ivar_w2 = models.FloatField(blank=True, null=True)
    flux_ivar_w3 = models.FloatField(blank=True, null=True)
    flux_ivar_w4 = models.FloatField(blank=True, null=True)
    mtype = models.TextField(blank=True, null=True)
    ref_cat = models.TextField(blank=True, null=True)
    ref_id = models.BigIntegerField(blank=True, null=True)
    parallax = models.FloatField(blank=True, null=True)
    parallax_ivar = models.FloatField(blank=True, null=True)
    pmra = models.FloatField(blank=True, null=True)
    pmra_ivar = models.FloatField(blank=True, null=True)
    pmdec = models.FloatField(blank=True, null=True)
    pmdec_ivar = models.FloatField(blank=True, null=True)
    gaia_phot_variable_flag = models.SmallIntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ls_dr10_q3c'


class MilliquasQ3C(models.Model):
    mid = models.AutoField(primary_key=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    name = models.CharField(max_length=32, blank=True, null=True)
    objtype = models.CharField(max_length=8, blank=True, null=True)
    rmag = models.FloatField(blank=True, null=True)
    bmag = models.FloatField(blank=True, null=True)
    comment = models.CharField(max_length=8, blank=True, null=True)
    rpsf = models.CharField(max_length=1, blank=True, null=True)
    bpsf = models.CharField(max_length=1, blank=True, null=True)
    z = models.FloatField(blank=True, null=True)
    namecit = models.CharField(max_length=8, blank=True, null=True)
    zcit = models.CharField(max_length=8, blank=True, null=True)
    qpct = models.IntegerField(blank=True, null=True)
    xname = models.CharField(max_length=32, blank=True, null=True)
    rname = models.CharField(max_length=32, blank=True, null=True)
    lobe1 = models.CharField(max_length=32, blank=True, null=True)
    lobe2 = models.CharField(max_length=32, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'milliquas_q3c'


class Ps1Q3C(models.Model):
    pid = models.BigAutoField(primary_key=True)
    objid = models.BigIntegerField(blank=True, null=True)
    psps_objid = models.BigIntegerField(blank=True, null=True)
    ra = models.FloatField()
    dec = models.FloatField()
    l = models.FloatField(blank=True, null=True)
    b = models.FloatField(blank=True, null=True)
    obj_class = models.CharField(max_length=8, blank=True, null=True)
    prob_galaxy = models.FloatField(blank=True, null=True)
    prob_star = models.FloatField(blank=True, null=True)
    prob_qso = models.FloatField(blank=True, null=True)
    extra_class = models.FloatField(blank=True, null=True)
    celld_class = models.FloatField(blank=True, null=True)
    cellid_class = models.IntegerField(blank=True, null=True)
    z_phot = models.FloatField(blank=True, null=True)
    z_err = models.FloatField(blank=True, null=True)
    z_zero = models.FloatField(blank=True, null=True)
    extra_photoz = models.IntegerField(blank=True, null=True)
    celld_photoz = models.FloatField(blank=True, null=True)
    cellid_photoz = models.IntegerField(blank=True, null=True)
    ps_score = models.FloatField(blank=True, null=True)
    objname = models.CharField(max_length=64, blank=True, null=True)
    objinfoflag = models.IntegerField(blank=True, null=True)
    qualityflag = models.IntegerField(blank=True, null=True)
    ndetections = models.IntegerField(blank=True, null=True)
    ramean = models.FloatField(blank=True, null=True)
    rameanerr = models.FloatField(blank=True, null=True)
    decmean = models.FloatField(blank=True, null=True)
    decmeanerr = models.FloatField(blank=True, null=True)
    gmeanpsfmag = models.FloatField(blank=True, null=True)
    gmeanpsfmagerr = models.FloatField(blank=True, null=True)
    rmeanpsfmag = models.FloatField(blank=True, null=True)
    rmeanpsfmagerr = models.FloatField(blank=True, null=True)
    imeanpsfmag = models.FloatField(blank=True, null=True)
    imeanpsfmagerr = models.FloatField(blank=True, null=True)
    zmeanpsfmag = models.FloatField(blank=True, null=True)
    zmeanpsfmagerr = models.FloatField(blank=True, null=True)
    ymeanpsfmag = models.FloatField(blank=True, null=True)
    ymeanpsfmagerr = models.FloatField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'ps1_q3c'


class RomaBzcatQ3C(models.Model):
    rid = models.AutoField(primary_key=True)
    name = models.CharField(max_length=16)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    l = models.FloatField(blank=True, null=True)
    b = models.FloatField(blank=True, null=True)
    z = models.FloatField(blank=True, null=True)
    z_err = models.FloatField(blank=True, null=True)
    rmag = models.FloatField(blank=True, null=True)
    classification = models.CharField(max_length=64, blank=True, null=True)
    flux = models.FloatField(blank=True, null=True)
    flux_143 = models.FloatField(blank=True, null=True)
    flux_xray = models.FloatField(blank=True, null=True)
    flux_fermi = models.FloatField(blank=True, null=True)
    aro = models.FloatField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'roma_bzcat_q3c'


class Sdss12PhotozQ3C(models.Model):
    sid = models.AutoField(primary_key=True)
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    mode = models.IntegerField(blank=True, null=True)
    q_mode = models.CharField(max_length=1, blank=True, null=True)
    classifier = models.IntegerField(blank=True, null=True)
    sdss12 = models.CharField(max_length=24, blank=True, null=True)
    m_sdss12 = models.CharField(max_length=1, blank=True, null=True)
    sdssid = models.CharField(max_length=32, blank=True, null=True)
    objid = models.CharField(max_length=24, blank=True, null=True)
    specid = models.CharField(max_length=32, blank=True, null=True)
    spobjid = models.CharField(max_length=24, blank=True, null=True)
    parentid = models.CharField(max_length=24, blank=True, null=True)
    flags = models.CharField(max_length=24, blank=True, null=True)
    status = models.IntegerField(blank=True, null=True)
    e_ra = models.FloatField(blank=True, null=True)
    e_dec = models.FloatField(blank=True, null=True)
    obsdate = models.FloatField(blank=True, null=True)
    quality = models.IntegerField(blank=True, null=True)
    umag = models.FloatField(blank=True, null=True)
    e_umag = models.FloatField(blank=True, null=True)
    gmag = models.FloatField(blank=True, null=True)
    e_gmag = models.FloatField(blank=True, null=True)
    rmag = models.FloatField(blank=True, null=True)
    e_rmag = models.FloatField(blank=True, null=True)
    imag = models.FloatField(blank=True, null=True)
    e_imag = models.FloatField(blank=True, null=True)
    zmag = models.FloatField(blank=True, null=True)
    e_zmag = models.FloatField(blank=True, null=True)
    zsp = models.FloatField(blank=True, null=True)
    e_zsp = models.FloatField(blank=True, null=True)
    f_zsp = models.IntegerField(blank=True, null=True)
    vdisp = models.FloatField(blank=True, null=True)
    e_vdisp = models.FloatField(blank=True, null=True)
    spinst = models.CharField(max_length=24, blank=True, null=True)
    sptype = models.CharField(max_length=24, blank=True, null=True)
    spclass = models.CharField(max_length=24, blank=True, null=True)
    spubclass = models.CharField(max_length=24, blank=True, null=True)
    spsignal = models.FloatField(blank=True, null=True)
    u_flags = models.CharField(max_length=24, blank=True, null=True)
    u_prob = models.IntegerField(blank=True, null=True)
    u_photo = models.IntegerField(blank=True, null=True)
    u_date = models.FloatField(blank=True, null=True)
    u_prime_mag = models.FloatField(blank=True, null=True)
    e_u_prime_mag = models.FloatField(blank=True, null=True)
    u_pmag = models.FloatField(blank=True, null=True)
    e_u_pmag = models.FloatField(blank=True, null=True)
    u_upmag = models.FloatField(blank=True, null=True)
    e_u_upmag = models.FloatField(blank=True, null=True)
    u_prad = models.FloatField(blank=True, null=True)
    e_u_prad = models.FloatField(blank=True, null=True)
    u_ora = models.FloatField(blank=True, null=True)
    u_odec = models.FloatField(blank=True, null=True)
    u_dvrad = models.FloatField(blank=True, null=True)
    u_dvell = models.FloatField(blank=True, null=True)
    u_pa = models.FloatField(blank=True, null=True)
    g_flags = models.CharField(max_length=24, blank=True, null=True)
    g_prob = models.IntegerField(blank=True, null=True)
    g_photo = models.IntegerField(blank=True, null=True)
    g_date = models.FloatField(blank=True, null=True)
    g_prime_mag = models.FloatField(blank=True, null=True)
    e_g_prime_mag = models.FloatField(blank=True, null=True)
    g_pmag = models.FloatField(blank=True, null=True)
    e_g_pmag = models.FloatField(blank=True, null=True)
    g_upmag = models.FloatField(blank=True, null=True)
    e_g_upmag = models.FloatField(blank=True, null=True)
    g_prad = models.FloatField(blank=True, null=True)
    e_g_prad = models.FloatField(blank=True, null=True)
    g_ora = models.FloatField(blank=True, null=True)
    g_odec = models.FloatField(blank=True, null=True)
    g_dvrad = models.FloatField(blank=True, null=True)
    g_dvell = models.FloatField(blank=True, null=True)
    g_pa = models.FloatField(blank=True, null=True)
    r_flags = models.CharField(max_length=24, blank=True, null=True)
    r_prob = models.IntegerField(blank=True, null=True)
    r_photo = models.IntegerField(blank=True, null=True)
    r_date = models.FloatField(blank=True, null=True)
    r_prime_mag = models.FloatField(blank=True, null=True)
    e_r_prime_mag = models.FloatField(blank=True, null=True)
    r_pmag = models.FloatField(blank=True, null=True)
    e_r_pmag = models.FloatField(blank=True, null=True)
    r_upmag = models.FloatField(blank=True, null=True)
    e_r_upmag = models.FloatField(blank=True, null=True)
    r_prad = models.FloatField(blank=True, null=True)
    e_r_prad = models.FloatField(blank=True, null=True)
    r_ora = models.FloatField(blank=True, null=True)
    r_odec = models.FloatField(blank=True, null=True)
    r_dvrad = models.FloatField(blank=True, null=True)
    r_dvell = models.FloatField(blank=True, null=True)
    r_pa = models.FloatField(blank=True, null=True)
    i_flags = models.CharField(max_length=24, blank=True, null=True)
    i_prob = models.IntegerField(blank=True, null=True)
    i_photo = models.IntegerField(blank=True, null=True)
    i_date = models.FloatField(blank=True, null=True)
    i_prime_mag = models.FloatField(blank=True, null=True)
    e_i_prime_mag = models.FloatField(blank=True, null=True)
    i_pmag = models.FloatField(blank=True, null=True)
    e_i_pmag = models.FloatField(blank=True, null=True)
    i_upmag = models.FloatField(blank=True, null=True)
    e_i_upmag = models.FloatField(blank=True, null=True)
    i_prad = models.FloatField(blank=True, null=True)
    e_i_prad = models.FloatField(blank=True, null=True)
    i_ora = models.FloatField(blank=True, null=True)
    i_odec = models.FloatField(blank=True, null=True)
    i_dvrad = models.FloatField(blank=True, null=True)
    i_dvell = models.FloatField(blank=True, null=True)
    i_pa = models.FloatField(blank=True, null=True)
    z_flags = models.CharField(max_length=24, blank=True, null=True)
    z_prob = models.IntegerField(blank=True, null=True)
    z_photo = models.IntegerField(blank=True, null=True)
    z_date = models.FloatField(blank=True, null=True)
    z_prime_mag = models.FloatField(blank=True, null=True)
    e_z_prime_mag = models.FloatField(blank=True, null=True)
    z_pmag = models.FloatField(blank=True, null=True)
    e_z_pmag = models.FloatField(blank=True, null=True)
    z_upmag = models.FloatField(blank=True, null=True)
    e_z_upmag = models.FloatField(blank=True, null=True)
    z_prad = models.FloatField(blank=True, null=True)
    e_z_prad = models.FloatField(blank=True, null=True)
    z_ora = models.FloatField(blank=True, null=True)
    z_odec = models.FloatField(blank=True, null=True)
    z_dvrad = models.FloatField(blank=True, null=True)
    z_dvell = models.FloatField(blank=True, null=True)
    z_pa = models.FloatField(blank=True, null=True)
    pmra = models.FloatField(blank=True, null=True)
    e_pmra = models.FloatField(blank=True, null=True)
    pmdec = models.FloatField(blank=True, null=True)
    e_pmdec = models.FloatField(blank=True, null=True)
    sigra = models.FloatField(blank=True, null=True)
    sigdec = models.FloatField(blank=True, null=True)
    m = models.IntegerField(blank=True, null=True)
    n = models.IntegerField(blank=True, null=True)
    g_o_plate = models.FloatField(blank=True, null=True)
    r_e_plate = models.FloatField(blank=True, null=True)
    g_j_plate = models.FloatField(blank=True, null=True)
    r_f_plate = models.FloatField(blank=True, null=True)
    i_n_plate = models.FloatField(blank=True, null=True)
    zph = models.FloatField(blank=True, null=True)
    e_zph = models.FloatField(blank=True, null=True)
    ave_zph = models.FloatField(blank=True, null=True)
    chi2 = models.FloatField(blank=True, null=True)
    abs_u_mag = models.FloatField(blank=True, null=True)
    abs_g_mag = models.FloatField(blank=True, null=True)
    abs_r_mag = models.FloatField(blank=True, null=True)
    abs_i_mag = models.FloatField(blank=True, null=True)
    abs_z_mag = models.FloatField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'sdss12photoz_q3c'


class TnsQ3C(models.Model):
    tid = models.AutoField(primary_key=True)
    objid = models.IntegerField(blank=True, null=True)
    name_prefix = models.CharField(max_length=4, blank=True, null=True)
    name = models.CharField(max_length=32, blank=True, null=True)
    ra = models.FloatField(blank=True, null=True)
    declination = models.FloatField(blank=True, null=True)
    redshift = models.FloatField(blank=True, null=True)
    typeid = models.IntegerField(blank=True, null=True)
    objtype = models.CharField(max_length=32, blank=True, null=True)
    reporting_groupid = models.IntegerField(blank=True, null=True)
    reporting_group = models.CharField(max_length=32, blank=True, null=True)
    source_groupid = models.IntegerField(blank=True, null=True)
    source_group = models.CharField(max_length=32, blank=True, null=True)
    discoverydate = models.DateTimeField(blank=True, null=True)
    discoverymag = models.FloatField(blank=True, null=True)
    discmagfilter = models.IntegerField(blank=True, null=True)
    filtername = models.CharField(max_length=24, blank=True, null=True)
    reporters = models.CharField(max_length=2048, blank=True, null=True)
    time_received = models.DateTimeField(blank=True, null=True)
    internal_names = models.CharField(max_length=256, blank=True, null=True)
    discovery_ads_bibcode = models.CharField(max_length=256, blank=True, null=True)
    class_ads_bibcodes = models.CharField(max_length=256, blank=True, null=True)
    creationdate = models.DateTimeField(blank=True, null=True)
    lastmodified = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tns_q3c'


class TomAlertsAlertstreammessage(models.Model):
    topic = models.CharField(max_length=500)
    message_id = models.CharField(max_length=50, blank=True, null=True)
    date_shared = models.DateTimeField()
    exchange_status = models.CharField(max_length=10)

    class Meta:
        managed = False
        db_table = 'tom_alerts_alertstreammessage'


class TomAlertsBrokerquery(models.Model):
    name = models.CharField(max_length=500)
    broker = models.CharField(max_length=50)
    parameters = models.JSONField()
    created = models.DateTimeField()
    modified = models.DateTimeField()
    last_run = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_alerts_brokerquery'


class TomCommonProfile(models.Model):
    affiliation = models.CharField(max_length=100, blank=True, null=True)
    user = models.OneToOneField(AuthUser, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_common_profile'


class TomDataproductsDataproduct(models.Model):
    product_id = models.CharField(unique=True, max_length=255, blank=True, null=True)
    data = models.CharField(max_length=100, blank=True, null=True)
    extra_data = models.TextField()
    created = models.DateTimeField()
    modified = models.DateTimeField()
    featured = models.BooleanField()
    observation_record = models.ForeignKey('TomObservationsObservationrecord', models.DO_NOTHING, blank=True, null=True)
    target = models.ForeignKey('TomTargetsBasetarget', models.DO_NOTHING)
    thumbnail = models.CharField(max_length=100, blank=True, null=True)
    data_product_type = models.CharField(max_length=50)

    class Meta:
        managed = False
        db_table = 'tom_dataproducts_dataproduct'


class TomDataproductsDataproductGroup(models.Model):
    dataproduct = models.ForeignKey(TomDataproductsDataproduct, models.DO_NOTHING)
    dataproductgroup = models.ForeignKey('TomDataproductsDataproductgroup', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_dataproducts_dataproduct_group'
        unique_together = (('dataproduct', 'dataproductgroup'),)


class TomDataproductsDataproductgroup(models.Model):
    name = models.CharField(max_length=200)
    created = models.DateTimeField()
    modified = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'tom_dataproducts_dataproductgroup'


class TomDataproductsReduceddatum(models.Model):
    data_type = models.CharField(max_length=100)
    source_name = models.CharField(max_length=100)
    source_location = models.CharField(max_length=200)
    timestamp = models.DateTimeField()
    value = models.JSONField()
    data_product = models.ForeignKey(TomDataproductsDataproduct, models.DO_NOTHING, blank=True, null=True)
    target = models.ForeignKey('TomTargetsBasetarget', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_dataproducts_reduceddatum'


class TomDataproductsReduceddatumMessage(models.Model):
    reduceddatum = models.ForeignKey(TomDataproductsReduceddatum, models.DO_NOTHING)
    alertstreammessage = models.ForeignKey(TomAlertsAlertstreammessage, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_dataproducts_reduceddatum_message'
        unique_together = (('reduceddatum', 'alertstreammessage'),)


class TomNonlocalizedeventsCredibleregion(models.Model):
    smallest_percent = models.IntegerField()
    candidate = models.ForeignKey('TomNonlocalizedeventsEventcandidate', models.DO_NOTHING)
    localization = models.ForeignKey('TomNonlocalizedeventsEventlocalization', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_nonlocalizedevents_credibleregion'
        unique_together = (('localization', 'candidate'),)


class TomNonlocalizedeventsEventcandidate(models.Model):
    nonlocalizedevent = models.ForeignKey('TomNonlocalizedeventsNonlocalizedevent', models.DO_NOTHING)
    target = models.ForeignKey('TomTargetsBasetarget', models.DO_NOTHING)
    viable = models.BooleanField()
    priority = models.IntegerField()
    viability_reason = models.TextField()
    healpix = models.BigIntegerField()
    created = models.DateTimeField()
    modified = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'tom_nonlocalizedevents_eventcandidate'
        unique_together = (('target', 'nonlocalizedevent'),)


class TomNonlocalizedeventsEventlocalization(models.Model):
    created = models.DateTimeField()
    modified = models.DateTimeField()
    nonlocalizedevent = models.ForeignKey('TomNonlocalizedeventsNonlocalizedevent', models.DO_NOTHING)
    date = models.DateTimeField()
    distance_mean = models.FloatField()
    distance_std = models.FloatField()
    area_50 = models.FloatField(blank=True, null=True)
    area_90 = models.FloatField(blank=True, null=True)
    skymap_hash = models.UUIDField(blank=True, null=True)
    skymap_url = models.CharField(max_length=200)
    skymap_version = models.SmallIntegerField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_nonlocalizedevents_eventlocalization'
        unique_together = (('nonlocalizedevent', 'skymap_hash'),)


class TomNonlocalizedeventsEventsequence(models.Model):
    sequence_id = models.IntegerField()
    event_subtype = models.CharField(max_length=256)
    created = models.DateTimeField()
    modified = models.DateTimeField()
    localization = models.ForeignKey(TomNonlocalizedeventsEventlocalization, models.DO_NOTHING, blank=True, null=True)
    nonlocalizedevent = models.ForeignKey('TomNonlocalizedeventsNonlocalizedevent', models.DO_NOTHING)
    details = models.JSONField(blank=True, null=True)
    ingestor_source = models.CharField(max_length=64)
    external_coincidence = models.ForeignKey('TomNonlocalizedeventsExternalcoincidence', models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_nonlocalizedevents_eventsequence'
        unique_together = (('nonlocalizedevent', 'sequence_id'),)


class TomNonlocalizedeventsExternalcoincidence(models.Model):
    details = models.JSONField(blank=True, null=True)
    localization = models.ForeignKey(TomNonlocalizedeventsEventlocalization, models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_nonlocalizedevents_externalcoincidence'


class TomNonlocalizedeventsNonlocalizedevent(models.Model):
    event_id = models.CharField(unique=True, max_length=64)
    created = models.DateTimeField()
    modified = models.DateTimeField()
    event_type = models.CharField(max_length=3)
    state = models.CharField(max_length=16)

    class Meta:
        managed = False
        db_table = 'tom_nonlocalizedevents_nonlocalizedevent'


class TomNonlocalizedeventsSkymaptile(models.Model):
    tile = models.TextField()  # This field type is a guess.
    probdensity = models.FloatField()
    localization = models.ForeignKey(TomNonlocalizedeventsEventlocalization, models.DO_NOTHING)
    distance_mean = models.FloatField()
    distance_std = models.FloatField()

    class Meta:
        managed = False
        db_table = 'tom_nonlocalizedevents_skymaptile'


class TomObservationsDynamiccadence(models.Model):
    cadence_strategy = models.CharField(max_length=100)
    cadence_parameters = models.JSONField()
    active = models.BooleanField()
    created = models.DateTimeField()
    modified = models.DateTimeField()
    observation_group = models.ForeignKey('TomObservationsObservationgroup', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_observations_dynamiccadence'


class TomObservationsObservationgroup(models.Model):
    name = models.CharField(max_length=50)
    created = models.DateTimeField()
    modified = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'tom_observations_observationgroup'


class TomObservationsObservationgroupObservationRecords(models.Model):
    observationgroup = models.ForeignKey(TomObservationsObservationgroup, models.DO_NOTHING)
    observationrecord = models.ForeignKey('TomObservationsObservationrecord', models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_observations_observationgroup_observation_records'
        unique_together = (('observationgroup', 'observationrecord'),)


class TomObservationsObservationrecord(models.Model):
    facility = models.CharField(max_length=50)
    parameters = models.JSONField()
    observation_id = models.CharField(max_length=255)
    status = models.CharField(max_length=200)
    created = models.DateTimeField()
    modified = models.DateTimeField()
    target = models.ForeignKey('TomTargetsBasetarget', models.DO_NOTHING)
    scheduled_end = models.DateTimeField(blank=True, null=True)
    scheduled_start = models.DateTimeField(blank=True, null=True)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_observations_observationrecord'


class TomObservationsObservationtemplate(models.Model):
    name = models.CharField(max_length=200)
    facility = models.CharField(max_length=50)
    parameters = models.JSONField()
    created = models.DateTimeField()
    modified = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'tom_observations_observationtemplate'


class TomSurveysSurveyfield(models.Model):
    name = models.CharField(primary_key=True, max_length=6)
    ra = models.FloatField()
    dec = models.FloatField()
    ecliptic_lng = models.FloatField()
    ecliptic_lat = models.FloatField()
    galactic_lng = models.FloatField()
    galactic_lat = models.FloatField()
    healpix = models.BigIntegerField()
    has_reference = models.BooleanField()

    class Meta:
        managed = False
        db_table = 'tom_surveys_surveyfield'


class TomSurveysSurveyfieldAdjacent(models.Model):
    from_surveyfield = models.ForeignKey(TomSurveysSurveyfield, models.DO_NOTHING)
    to_surveyfield = models.ForeignKey(TomSurveysSurveyfield, models.DO_NOTHING, related_name='tomsurveyssurveyfieldadjacent_to_surveyfield_set')

    class Meta:
        managed = False
        db_table = 'tom_surveys_surveyfield_adjacent'
        unique_together = (('from_surveyfield', 'to_surveyfield'),)


class TomSurveysSurveyobservationrecord(models.Model):
    id = models.BigAutoField(primary_key=True)
    facility = models.CharField(max_length=50)
    parameters = models.JSONField()
    observation_id = models.CharField(max_length=255)
    status = models.CharField(max_length=200)
    scheduled_start = models.DateTimeField(blank=True, null=True)
    scheduled_end = models.DateTimeField(blank=True, null=True)
    created = models.DateTimeField()
    modified = models.DateTimeField()
    survey_field = models.ForeignKey(TomSurveysSurveyfield, models.DO_NOTHING)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_surveys_surveyobservationrecord'


class TomTargetsBasetarget(models.Model):
    name = models.CharField(unique=True, max_length=100)
    type = models.CharField(max_length=100)
    created = models.DateTimeField()
    modified = models.DateTimeField()
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    epoch = models.FloatField(blank=True, null=True)
    parallax = models.FloatField(blank=True, null=True)
    pm_ra = models.FloatField(blank=True, null=True)
    pm_dec = models.FloatField(blank=True, null=True)
    galactic_lng = models.FloatField(blank=True, null=True)
    galactic_lat = models.FloatField(blank=True, null=True)
    distance = models.FloatField(blank=True, null=True)
    distance_err = models.FloatField(blank=True, null=True)
    mean_anomaly = models.FloatField(blank=True, null=True)
    arg_of_perihelion = models.FloatField(blank=True, null=True)
    eccentricity = models.FloatField(blank=True, null=True)
    lng_asc_node = models.FloatField(blank=True, null=True)
    inclination = models.FloatField(blank=True, null=True)
    mean_daily_motion = models.FloatField(blank=True, null=True)
    semimajor_axis = models.FloatField(blank=True, null=True)
    ephemeris_period = models.FloatField(blank=True, null=True)
    ephemeris_period_err = models.FloatField(blank=True, null=True)
    ephemeris_epoch = models.FloatField(blank=True, null=True)
    ephemeris_epoch_err = models.FloatField(blank=True, null=True)
    epoch_of_perihelion = models.FloatField(blank=True, null=True)
    scheme = models.CharField(max_length=50)
    perihdist = models.FloatField(blank=True, null=True)
    epoch_of_elements = models.FloatField(blank=True, null=True)
    permissions = models.CharField(max_length=100)

    class Meta:
        managed = False
        db_table = 'tom_targets_basetarget'


class TomTargetsPersistentshare(models.Model):
    destination = models.CharField(max_length=200)
    created = models.DateTimeField()
    target = models.ForeignKey(TomTargetsBasetarget, models.DO_NOTHING)
    user = models.ForeignKey(AuthUser, models.DO_NOTHING, blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_targets_persistentshare'
        unique_together = (('target', 'destination'),)


class TomTargetsTargetextra(models.Model):
    key = models.CharField(max_length=200)
    value = models.TextField()
    target = models.ForeignKey(TomTargetsBasetarget, models.DO_NOTHING)
    bool_value = models.BooleanField(blank=True, null=True)
    float_value = models.FloatField(blank=True, null=True)
    time_value = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = 'tom_targets_targetextra'
        unique_together = (('target', 'key'),)


class TomTargetsTargetlist(models.Model):
    name = models.CharField(max_length=200)
    created = models.DateTimeField()
    modified = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'tom_targets_targetlist'


class TomTargetsTargetlistTargets(models.Model):
    targetlist = models.ForeignKey(TomTargetsTargetlist, models.DO_NOTHING)
    basetarget = models.ForeignKey(TomTargetsBasetarget, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_targets_targetlist_targets'
        unique_together = (('targetlist', 'basetarget'),)


class TomTargetsTargetname(models.Model):
    name = models.CharField(unique=True, max_length=100)
    created = models.DateTimeField()
    target = models.ForeignKey(TomTargetsBasetarget, models.DO_NOTHING)
    modified = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'tom_targets_targetname'


class TomTreasuremapTreasuremappointing(models.Model):
    id = models.BigAutoField(primary_key=True)
    treasuremap_id = models.IntegerField(unique=True)
    status = models.CharField(max_length=200)
    nonlocalizedevent = models.ForeignKey(TomNonlocalizedeventsNonlocalizedevent, models.DO_NOTHING)
    observation_record = models.ForeignKey(TomSurveysSurveyobservationrecord, models.DO_NOTHING)

    class Meta:
        managed = False
        db_table = 'tom_treasuremap_treasuremappointing'
        unique_together = (('nonlocalizedevent', 'observation_record', 'status'),)
