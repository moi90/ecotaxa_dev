# -*- coding: utf-8 -*-
from appli import db, database , PrintInCharte,gvp,gvg,EncodeEqualList,DecodeEqualList
from flask import  render_template, g, flash,request
import logging
import time,re
from appli.tasks.taskmanager import AsyncTask,DoTaskClean
from appli.database import GetAll,ExecSQL
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn import svm

class TaskClassifAuto(AsyncTask):
    class Params (AsyncTask.Params):
        def __init__(self,InitStr=None):
            self.steperrors=[]
            super().__init__(InitStr)
            if InitStr is None: # Valeurs par defaut ou vide pour init
                self.Methode='randomforest'
                self.ProjectId=None
                self.BaseProject=None
                self.CritVar=None
                self.Taxo=""
                self.TargetTaxo= ""
                self.Perimeter=""
                self.resetpredicted=""
                self.keeplog="no"
                self.learninglimit=""
                self.CustSettings={}

    def __init__(self,task=None):
        super().__init__(task)
        self.pgcur=db.engine.raw_connection().cursor()
        if task is None:
            self.param=self.Params()
        else:
            self.param=self.Params(task.inputparam)

    def SPCommon(self):
        logging.info("Execute SPCommon for Automatic Classification Task %d"%self.task.id)

    def SPStep1(self):
        logging.info("Input Param = %s"%(self.param.__dict__,))
        logging.info("Start Step 1")
        self.UpdateProgress(1,"Retrieve Data from Learning Set")
        TInit = time.time()
        Prj=database.Projects.query.filter_by(projid=self.param.ProjectId).first()
        PrjBase=database.Projects.query.filter_by(projid=self.param.BaseProject).first()
        MapPrj=self.GetReverseObjMap(Prj)
        MapPrjBase=self.GetReverseObjMap(PrjBase)
        logging.info("MapPrj %s",MapPrj)
        logging.info("MapPrjBase %s",MapPrjBase)
        CritVar=self.param.CritVar.split(",")
        ColsPrj=[]  # contient les colonnes communes au deux projets dans le même ordre
        ColsPrjBase=[]
        MapColTargetToBase={}
        PostTaxoMapping={}
        if 'posttaxomapping' in self.param.CustSettings:
            PostTaxoMapping = {int(el[0].strip()):int(el[1].strip()) for el in [el.split(':') for el in self.param.CustSettings['posttaxomapping'].split(',')]}
            logging.info("PostTaxoMapping = %s ", PostTaxoMapping )
        if self.param.learninglimit:
            self.param.learninglimit=int(self.param.learninglimit) # convert
        for c in CritVar:
            if c not in MapPrj:
                logging.info("Variable %s not available in the classified project",c)
            elif c not in MapPrjBase:
                logging.info("Variable %s not available in the base project",c)
            else:
                ColsPrjBase.append(MapPrjBase[c])
                ColsPrj.append(MapPrj[c])
                MapColTargetToBase[MapPrj[c]]=MapPrjBase[c]
        sql="select 1"
        for c in ColsPrjBase:
            sql+=",coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY {0}),-9999) as {0}".format(c)
        sql+=" from objects where projid={0} and classif_id is not null and classif_qual='V'".format(PrjBase.projid)
        if self.param.learninglimit:
            sql += """ and objid in ( select objid from (
                        select objid,row_number() over(PARTITION BY classif_id order by random_value) rang
                        from obj_head
                        where projid={0} and classif_id is not null
                        and classif_qual='V' ) q where rang <={1} ) """.format(PrjBase.projid,self.param.learninglimit)
        DefVal=GetAll(sql)[0]
        sql="select objid,classif_id"
        for c in ColsPrjBase:
            sql+=",coalesce({0},{1}) ".format(c,DefVal[c])
        sql+=""" from objects
                    where classif_id is not null and classif_qual='V'
                    and projid={0}
                    and classif_id in ({1}) """.format(PrjBase.projid,self.param.Taxo,self.param.learninglimit)
        if self.param.learninglimit:
            sql += """ and objid in ( select objid from (
                        select objid,row_number() over(PARTITION BY classif_id order by random_value) rang
                        from obj_head
                        where projid={0} and classif_id is not null
                        and classif_qual='V' ) q where rang <={1} ) """.format(PrjBase.projid,self.param.learninglimit)
        sql += " order by objid "
        DBRes=np.array(GetAll(sql))
        # Ids = DBRes[:,0] # Que l'objid
        learn_cat = DBRes[:,1] # Que la classif
        learn_var = DBRes[:,2:] # exclu l'objid & la classif
        DBRes=None # libere la mémoire
        logging.info('DB Conversion to NP : %0.3f s', time.time() - TInit)
        logging.info("Variable shape %d Row, %d Col",*learn_var.shape)
        # Note : La multiplication des jobs n'est pas forcement plus performante, en tous cas sur un petit ensemble.
        if  self.param.Methode=='randomforest':
            Classifier = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, n_jobs=1,class_weight = "balanced")
        elif  self.param.Methode=='svm':
            Classifier = svm.SVC()
        else:
            raise Exception ("Classifier '%s' not implemented "%self.param.Methode)
        if self.param.Perimeter!='all':
            PerimeterWhere=" and ( classif_qual='P' or classif_qual is null)  "
        else: PerimeterWhere=""
        if self.param.TargetTaxo!="":
            PerimeterWhere += " and classif_id in (%s) "%(self.param.TargetTaxo,)
        if self.param.resetpredicted == "ResetToPredicted" and self.param.TargetTaxo!="":
            sqlhisto="""insert into objectsclassifhisto(objid,classif_date,classif_type,classif_id,classif_qual,classif_who)
                          select objid,classif_when,'M', classif_id,classif_qual,classif_who
                            from obj_head o
                            where projid={0} and classif_when is not null and classif_qual='V'
                            and classif_id in ({1})""".format(Prj.projid,self.param.TargetTaxo)
            ExecSQL(sqlhisto)
            sqlhisto = """update obj_head set classif_qual=null
                            where projid={0} and classif_when is not null and classif_qual='V'
                            and classif_id in ({1}) """.format(Prj.projid,self.param.TargetTaxo)
            ExecSQL(sqlhisto)

        # TStep = time.time()
        # cette solution ne convient pas, car lorsqu'on l'applique par bloc de 100 parfois il n'y a pas de valeur dans
        # toute la colonne et du coup la colonne est supprimé car on ne peut pas calculer la moyenne.
        # learn_var = Imputer().fit_transform(learn_var)
        #learn_var[learn_var==np.nan] = -99999 Les Nan sont des NULL dans la base traités parle coalesce
        # logging.info('Clean input variables :  %0.3f s', time.time() - TStep)
        TStep = time.time()
        Classifier.fit(learn_var, learn_cat)
        logging.info('Model fit duration :  %0.3f s', time.time() - TStep)
        NbrItem=GetAll("select count(*) from obj_head where projid={0} {1} ".format(Prj.projid,PerimeterWhere))[0][0]
        if NbrItem==0:
            raise Exception ("No object to classify, perhaps all object already classified or you should adjust the perimeter settings as it was probably set to 'Not Validated' ")
        sql="select objid"
        for c in ColsPrj:
            sql+=",coalesce({0},{1}) ".format(c,DefVal[MapColTargetToBase[c]])
        sql+=""" from objects
                    where projid={0} {1}
                    order by objid""".format(Prj.projid,PerimeterWhere)
        self.pgcur.execute(sql)
        upcur=db.engine.raw_connection().cursor()
        ProcessedRows=0
        while True:
            self.UpdateProgress(15+85*(ProcessedRows/NbrItem),"Processed %d/%d"%(ProcessedRows,NbrItem))
            TStep = time.time()
            # recupère les variables des objets à classifier
            DBRes=np.array(self.pgcur.fetchmany(100))
            if len(DBRes)==0:
                break
            ProcessedRows+=len(DBRes)
            Tget_Ids = DBRes[:,0] # Que l'objid
            Tget_var = DBRes[:,1:] # exclu l'objid
            TStep2 = time.time()
            # Tget_var= Imputer().fit_transform(Tget_var) # voir commentaire sur learn_var
            # Tget_var[Tget_var==np.nan] = -99999
            Result=Classifier.predict_proba(Tget_var)
            ResultMaxCol=np.argmax(Result,axis=1)
            # Typage important pour les perf postgresql
            SqlParam=[{'cat':int(Classifier.classes_[mc]),'p':r[mc],'id':int(i)} for i,mc,r in zip(Tget_Ids,ResultMaxCol,Result)]
            for i, v in enumerate(SqlParam):
                if v['cat'] in PostTaxoMapping:
                    SqlParam[i]['cat']=PostTaxoMapping[v['cat']]
            TStep3 = time.time()
            # MAJ dans la base, Si pas de classif devient predicted , Si vide ou predicted, MAJ de la classif
            if self.param.keeplog:
                upcur.executemany("""insert into objectsclassifhisto(objid,classif_date,classif_type,classif_id,classif_qual,classif_score)
                                      select objid,classif_auto_when,'A', classif_auto_id,classif_qual,classif_auto_score
                                        from obj_head
                                        where objid=%(id)s and classif_auto_id!=%(cat)s and classif_auto_id is not null
                                        and classif_auto_when is not null """,SqlParam)
            upcur.executemany("""update obj_head set classif_auto_id=%(cat)s,classif_auto_score=%(p)s,classif_auto_when=now()
                                    ,classif_qual=case when classif_qual in ('D','V') then  classif_qual else 'P'  END
                                    ,classif_id=case when classif_qual in ('D','V') then classif_id  else %(cat)s end
                                    where objid=%(id)s""",SqlParam)
            upcur.connection.commit()
            logging.info('Chunk Db Extract %d/%d, Classification and Db Save :  %0.3f s %0.3f+%0.3f+%0.3f'
                         , ProcessedRows ,NbrItem
                         , time.time() - TStep,TStep2 - TStep,TStep3 - TStep2,time.time() - TStep3)


        self.task.taskstate="Done"
        self.UpdateProgress(100,"Classified %d objects"%ProcessedRows)
        # self.task.taskstate="Done"
        # self.UpdateProgress(100,"Processing done")
        # if self.param.IntraStep==1:
        #sinon on pose une question


    def QuestionProcess(self):
        Prj=database.Projects.query.filter_by(projid=gvg("p")).first()
        if not Prj.CheckRight(1):
            return PrintInCharte("ACCESS DENIED for this project<br>")
        g.prjtitle=Prj.title
        g.headcenter="<h4><a href='/prj/{0}'>{1}</a></h4>".format(Prj.projid,Prj.title)
        txt=""
        errors=[]
        if self.task.taskstep==0:
            if gvg('src')=="":
                # Premier écran de configuration, choix du projet de base
                txt+="""<h3>AUTOMATIC CLASSIFICATION : Selection of Learning Set</h3>
                <p>A Learning set is a project containing validated data &nbsp;<span data-toggle="tooltip" data-placement="right" data-html="true" title="
        <p style='text-align: left'>You can run the SUBSET tool to :<br><ul style='text-align: left'>
        <li>Create a Learning set that you will later improve
        <li>Check validation of a project using a randomly limited number of objects from the project</ul></p>"
        class="glyphicon glyphicon-question-sign"></span></p>
        <script>
        $(function () {  $('[data-toggle="tooltip"]').tooltip()})
        </script>
                """
                d=DecodeEqualList(Prj.classifsettings)
                if d.get("baseproject","")!="":
                    BasePrj=GetAll("select projid,title from projects where projid=%s"
                           # ,(d.get("baseproject"),))
                           , (re.findall(r'\d+',d.get("baseproject"))[0],))  #permet de gérer le fait que la V2 peut avoir un LS de plusieurs projets
                    if len(BasePrj):
                        txt+="""<a class='btn btn-primary' href='/Task/Create/TaskClassifAuto?p={0}&src={1}'>
                        USE previous Learning Set : #{1} - {2}</a><br><br>OR USE another project<br><br>""".format(Prj.projid,*BasePrj[0])
                from flask_login import current_user
                sql="select projid,title,status,coalesce(objcount,0),coalesce(pctvalidated,0),coalesce(pctclassified,0) from projects "
                if not current_user.has_role(database.AdministratorLabel):
                    sql+=" where projid in (select projid from projectspriv where member=%d)"%current_user.id
                sql+=" order by title"
                ProjList=database.GetAll(sql)
                txt+="""<table class='table table-bordered table-verycondensed table-hover'><tr><th width=100>ID</td><th>Title</td><th width=100>Status</td><th width=100>Nbr Obj</td>
            <th width=100>% Validated</td><th width=100>% Classified</td></tr>"""
                for r in ProjList:
                    txt+="""<tr><td><a class="btn btn-xs btn-primary" href='/Task/Create/TaskClassifAuto?p={0}&src={1}'>Select #{1}</a></td>
                    <td>{2}</td>
                    <td>{3}</td>
                    <td>{4:0.0f}</td>
                    <td>{5:0.2f}</td>
                    <td>{6:0.2f}</td>
                    </tr>""".format(Prj.projid,*r)
                txt+="</table>"
                return PrintInCharte(txt)
            # Le projet de base est choisi second écran ou validation du second ecran
            if gvp('starttask')=="Y":
                # validation du second ecran
                self.param.ProjectId=gvg("p")
                self.param.BaseProject=gvg("src")
                self.param.Methode=gvp("Methode")
                self.param.CritVar=gvp("CritVar")
                self.param.Perimeter=gvp("Perimeter")
                self.param.learninglimit = gvp("learninglimit")
                self.param.keeplog=gvp("keeplog")
                self.param.resetpredicted = gvp("resetpredicted")
                self.param.Taxo=",".join( (x[4:] for x in request.form if x[0:4]=="taxo") )
                self.param.TargetTaxo = ",".join((x[10:] for x in request.form if x[0:10] == "targettaxo"))
                self.param.CustSettings=DecodeEqualList(gvp("TxtCustSettings"))
                g.TxtCustSettings=gvp("TxtCustSettings")
                # Verifier la coherence des données
                if self.param.Methode=='' : errors.append("You must select a classification method")
                if self.param.CritVar=='' : errors.append("You must select some variable")
                if self.param.Taxo=='' : errors.append("You must select some category")
                if len(errors)>0:
                    for e in errors:
                        flash(e,"error")
                else: # Pas d'erreur, on memorize les parametres dans le projet et on lance la tache
                    # On ajoute les valeurs dans CustSettings pour les sauver dans le ClassifSettings du projet
                    d=self.param.CustSettings.copy()
                    d['critvar']=self.param.CritVar
                    d['methode']=self.param.Methode
                    d['perimeter']=self.param.Perimeter
                    d['learninglimit'] = self.param.learninglimit
                    d['baseproject']=self.param.BaseProject
                    d['seltaxo']=self.param.Taxo
                    Prj.classifsettings=EncodeEqualList(d)
                    return self.StartTask(self.param)
            else: # valeurs par default
                d=DecodeEqualList(Prj.classifsettings)
                # Certaines variable on leur propre zone d'edition, les autres sont dans la zone texte custom settings
                self.param.CritVar=d.get("critvar","")
                self.param.Methode=d.get("methode","")
                self.param.Taxo=d.get("seltaxo","")
                self.param.Perimeter=d.get("perimeter","nmc")
                self.param.learninglimit = d.get("learninglimit", "")
                if "critvar" in d : del d["critvar"]
                if "methode" in d : del d["methode"]
                if "perimeter" in d : del d["perimeter"]
                if "learninglimit" in d: del d["learninglimit"]
                if "seltaxo" in d : del d["seltaxo"]
                if "baseproject" in d : del d["baseproject"]
                g.TxtCustSettings=EncodeEqualList(d)
            # Le projet de base est choisi second écran
            #recupere les categories et le nombre d'occurence dans le projet de base/learning
            sql="""select n.classif_id,t.name||case when p1.name is not null and t.name not like '%% %%'  then ' ('||p1.name||')' else ' ' end as name
                    ,n.nbr
                    from (select o.classif_id,count(*) nbr
                          from obj_head o where projid =%(projid)s and classif_qual='V'
                          group by classif_id) n
                    JOIN taxonomy t on n.classif_id=t.id
                    left join taxonomy p1 on t.parent_id=p1.id
                    order by nbr desc,name"""
            g.TaxoList=GetAll(sql,{"projid":gvg("src")},cursor_factory=None)
            s=sum([r[2] for r in g.TaxoList])  # Nbr total d'objet par categorie
            g.TaxoList=[[r[0],r[1],r[2],round(100*r[2]/s,1),'checked'] for r in g.TaxoList] # Ajout du % d'objet par categorie
            # print("taxo ="+self.param.Taxo)
            if self.param.Taxo!='':
                TaxoSel=set([int(x) for x in self.param.Taxo.split(",")])
                for i,r in zip(range(10000),g.TaxoList):
                    if r[0] not in TaxoSel:
                        g.TaxoList[i][4]=""
            # Récupère la liste des taxo de la cible pour laisser l'option de ne faire de la prediction que sur un sous ensemble de la destination
            sql="""select t.id,concat(t.name,'(',t2.name,')') as name from (
                    select distinct classif_id
                    from obj_head
                    where projid=%(projid)s and classif_id is not null  ) q
                join taxonomy t on q.classif_id=t.id
                left join taxonomy t2 on t.parent_id=t2.id
                order by name """
            g.TargetTaxoList = GetAll(sql, {"projid": Prj.projid}, cursor_factory=None)
            # Determination des criteres/variables utilisées par l'algo de learning
            revobjmap = self.GetReverseObjMap(Prj)
            PrjBase=database.Projects.query.filter_by(projid=gvg("src")).first()
            revobjmapbase = self.GetReverseObjMap(PrjBase)
            critlist={k:[k,0,0,0,0] for k in revobjmap.keys() if k in revobjmapbase}
            sql="select count(*) nbrtot,count(case classif_qual when 'V' then null else 1 end) nbrnotval"
            for k,v in revobjmap.items():
                if k in revobjmapbase:
                    sql+=",count({0}) {0}_nbr,count(distinct case classif_qual when 'V' then {0} end) {0}_nbrdist,count(case classif_qual when 'V' then null else {0} end) {0}_nbrnv".format(v)
            sql+=" from (select * from objects where projid={0} LIMIT 50000 ) objects ".format(Prj.projid)
            stat=GetAll(sql)[0]
            for k,v in revobjmap.items():
                if k in revobjmapbase:
                    if stat["nbrtot"]:
                        critlist[k][3]=round(100*stat[v+"_nbr"]/stat["nbrtot"])
                    if stat["nbrnotval"]:
                        critlist[k][4]=round(100*stat[v+"_nbrnv"]/stat["nbrnotval"])
            if Prj.projid!=PrjBase.projid:
                sql="select count(*) nbrtot,0 nbrnotval"
                for k,v in revobjmap.items():
                    if k in revobjmapbase:
                        sql+=",count({0}) {0}_nbr,0 {0}_nbrnv,count(distinct {0}) {0}_nbrdist".format(v)
                sql+=" from (select * from objects where projid={0} and classif_qual='V' LIMIT 50000 ) objects ".format(PrjBase.projid)
                stat=GetAll(sql)[0]
            if (stat["nbrtot"]-stat["nbrnotval"])>0:
                for k,v in revobjmap.items():
                    if k in revobjmapbase:
                        critlist[k][1]=round(100*(stat[v+"_nbr"]-stat[v+"_nbrnv"])/(stat["nbrtot"]-stat["nbrnotval"]))
                        critlist[k][2]=round(stat[v+"_nbrdist"])

            g.critlist=list(critlist.values())
            g.critlist.sort(key=lambda t: t[0])
            # app.logger.info(revobjmap)
            return render_template('task/classifauto_create.html',header=txt,data=self.param)

    def GetReverseObjMap(self, Prj):
        revobjmap = {v: k for k, v in DecodeEqualList(Prj.mappingobj).items() if k[0] == 'n'}
        revobjmap['depth_min'] = 'depth_min'
        revobjmap['depth_max'] = 'depth_max'
        return revobjmap

    def GetDoneExtraAction(self):
        # si le status est demandé depuis le monitoring ca veut dire que l'utilisateur est devant,
        # on efface donc la tache et on lui propose d'aller sur la classif manuelle
        PrjId=self.param.ProjectId
        time.sleep(1)
        DoTaskClean(self.task.id)
        return """<a href='/prj/{0}' class='btn btn-primary btn-sm'  role=button>Go to Manual Classification Screen</a>
        <a href='/prjcm/{0}' class='btn btn-primary btn-sm'  role=button>Go to Confusion Matrix</a> """.format(PrjId)

