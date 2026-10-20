drop table batch.LLIDS3218_test_codes_table ;

create table batch.LLIDS3218_test_codes_table as 
select code, descr, short_descr, domain, editdt createdt 
from sid.codes where domain = 'COCO REL TYPE';

insert into batch.LLIDS3218_test_codes_table 
select code, descr, short_descr, domain, editdt createdt 
from sid.codes where domain = 'CO ROLE';
